from __future__ import annotations

# 应用编排层：把参数、题目服务、核心运行器组织成可执行流程。
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# 核心能力：运行器、配置对象、日志初始化。
from pipeline_core import PPIOJudgeRunner, RuntimeConfig, SamplingConfig, setup_logger

# 应用层子模块：题目服务 + 常量配置。
from .case_service import CaseService
from .constants import (
    DEFAULT_CASES_FILE,
    DEFAULT_MATH_QUIZ_OUTPUT_DIR,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RUN_NAME_PREFIX,
    DOMAIN_PRESETS,
)
from .math_quiz import MathQuizRunner, MathQuizRuntimeConfig, parse_math_ids, parse_question_types


class PipelineApplication:
    """应用层编排器：将参数、题目、运行器串联起来。"""

    def __init__(self, args: argparse.Namespace):
        # 保存 CLI 解析后的参数对象。
        self.args = args
        # 记录是否仍为默认值，避免后续覆盖用户显式传参。
        self._cases_file_is_default = self.args.cases_file == DEFAULT_CASES_FILE
        self._output_dir_is_default = self.args.output_dir == DEFAULT_OUTPUT_DIR
        self._math_quiz_output_dir_is_default = self.args.math_quiz_output_dir == DEFAULT_MATH_QUIZ_OUTPUT_DIR
        self._run_name_prefix_is_default = self.args.run_name_prefix == DEFAULT_RUN_NAME_PREFIX
        # 先根据运行模式调整默认输出目录（仅在用户未显式传参时覆盖）。
        self.apply_mode_defaults()
        # 初始化阶段先应用 domain 默认值（仅在用户未显式指定时）。
        self.apply_domain_defaults()

    def apply_mode_defaults(self) -> None:
        """按运行模式应用默认输出目录。"""

        # 代码评测默认输出目录改为 runs/code。
        if self._output_dir_is_default:
            self.args.output_dir = DOMAIN_PRESETS["code"]["output_dir"]

        # 数学测验默认输出目录改为 runs/math。
        if self._math_quiz_output_dir_is_default:
            self.args.math_quiz_output_dir = DOMAIN_PRESETS["math"]["output_dir"]

    def apply_domain_defaults(self) -> None:
        """按业务域应用隔离默认值：仅在用户未显式改参时生效。"""

        # 读取 domain，缺省视为 general（不强制覆盖默认路径）。
        domain = getattr(self.args, "domain", "general")
        if domain not in DOMAIN_PRESETS:
            return

        # 取出该 domain 对应的预设路径。
        preset = DOMAIN_PRESETS[domain]

        # 仅当用户沿用默认参数时才覆盖，避免意外改掉用户自定义值。
        if self._cases_file_is_default:
            self.args.cases_file = preset["cases_file"]
        if self._output_dir_is_default:
            self.args.output_dir = preset["output_dir"]
        if self._run_name_prefix_is_default:
            self.args.run_name_prefix = preset["run_name_prefix"]

    def init_domain_layout(self) -> Dict[str, Any]:
        """初始化 code/math 隔离目录结构，并生成空题库模板。"""

        # 若指定了具体域，则只初始化该域；否则同时初始化 code/math。
        domain = getattr(self.args, "domain", "general")
        domains = [domain] if domain in DOMAIN_PRESETS else list(DOMAIN_PRESETS.keys())

        # 用于返回给调用方的创建结果摘要。
        created_dirs: List[str] = []
        created_files: List[str] = []

        # 逐域创建目录与默认题库文件。
        for one_domain in domains:
            preset = DOMAIN_PRESETS[one_domain]
            cases_path = Path(preset["cases_file"])
            output_dir = Path(preset["output_dir"])

            # 1) 创建题库目录与输出目录。
            cases_path.parent.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            created_dirs.extend([str(cases_path.parent), str(output_dir)])

            # 2) 若题库文件不存在，则写入空模板。
            if not cases_path.exists():
                cases_path.write_text('{\n  "cases": []\n}\n', encoding="utf-8")
                created_files.append(str(cases_path))

        # 3) 返回初始化结果供 CLI 打印。
        return {
            "domains": domains,
            "created_dirs": sorted(set(created_dirs)),
            "created_files": created_files,
        }

    def build_default_sampling(self) -> SamplingConfig:
        """根据 CLI 参数构造默认采样配置。"""

        return SamplingConfig(
            temperature=self.args.temperature,
            top_p=self.args.top_p,
            frequency_penalty=self.args.frequency_penalty,
            max_tokens=self.args.max_tokens,
            top_k=self.args.top_k,
        )

    def build_runtime(self) -> RuntimeConfig:
        """根据 CLI 参数构造运行时配置（接口与执行策略）。"""

        return RuntimeConfig(
            ppio_api_key=self.args.ppio_api_key,
            ppio_base_url=self.args.ppio_base_url,
            ppio_model=self.args.model,
            judge0_base_url=self.args.judge0_base_url,
            judge0_language_id=self.args.language_id,
            samples_per_problem=self.args.samples,
            poll_interval_sec=self.args.poll_interval,
            poll_timeout_sec=self.args.poll_timeout,
            output_dir=Path(self.args.output_dir),
            final_pick_strategy=self.args.final_pick_strategy,
        )

    def run(self) -> Dict[str, Any]:
        """执行主流程，并返回结构化结果摘要。"""

        # A) 数学客观题测验模式（与代码生成判题流程隔离）。
        if getattr(self.args, "run_math_quiz", False):
            if not self.args.ppio_api_key:
                raise RuntimeError("未提供 PPIO API Key，请使用 --ppio-api-key 或设置环境变量 PPIO_API_KEY")

            # 1) 解析题型参数：支持“判断,单选,多选”这类组合写法。
            question_types = parse_question_types(self.args.math_question_types)

            # 解析 --math-ids（空字符串 -> None，表示不启用 ID 抽样）
            raw_ids = getattr(self.args, "math_ids", "") or ""
            ids = parse_math_ids(raw_ids) if raw_ids.strip() else None

            # 2) 组装数学测验独立运行配置：仅用于客观题问答判分，不涉及 Judge0。
            runtime = MathQuizRuntimeConfig(
                ppio_api_key=self.args.ppio_api_key,
                ppio_base_url=self.args.ppio_base_url,
                ppio_model=self.args.model,
                question_types=question_types,
                max_cases=max(0, int(self.args.math_max_cases)),
                samples=max(1, int(getattr(self.args, "math_samples", 4))),
                request_interval=float(getattr(self.args, "math_request_interval", 0.5)),
                temperature=float(self.args.math_temperature),
                top_p=float(self.args.math_top_p),
                max_tokens=int(self.args.math_max_tokens),
            )

            # 3) 创建本次数学测验运行目录。
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_dir = Path(self.args.math_quiz_output_dir) / f"{self.args.math_quiz_run_name_prefix}_{run_id}"
            run_dir.mkdir(parents=True, exist_ok=True)

            # 4) 使用独立 logger 名称，避免与代码评测流程日志混淆。
            logger = setup_logger(run_dir / "run.log", logger_name="ppio_math_quiz")

            # 5) 执行数学测验主流程并返回统计摘要。
            return MathQuizRunner(runtime, logger).run(
                cases_file=Path(self.args.math_cases_file),
                run_dir=run_dir,
                ids=ids,
            )

        # 0) 预先准备常用对象。
        cases_file = Path(self.args.cases_file)
        default_sampling = self.build_default_sampling()
        case_service = CaseService(default_sampling=default_sampling, samples_per_problem=self.args.samples)

        # 1) 若是目录初始化模式：创建后直接返回，不进入评测流程。
        if self.args.init_domain_layout:
            return {
                "mode": "init_domain_layout",
                **self.init_domain_layout(),
            }

        # 仅做 Excel -> cases 的导出，不执行评测。
        if self.args.export_first_from_excel:
            case_service.export_first_problem_from_excel(Path(self.args.export_first_from_excel), cases_file)
            return {
                "mode": "export_only",
                "exported_cases_file": str(cases_file),
            }

        # 2) 参数组合校验：regen 必须配合 demo 模式。
        if self.args.regen_cases and not self.args.use_demo_cases:
            raise ValueError("--regen-cases 需要和 --use-demo-cases 一起使用")

        # 3) demo 模式：按需生成/重建题库文件。
        if self.args.use_demo_cases:
            case_service.ensure_demo_cases_file(cases_file, regen=self.args.regen_cases)

        # 4) 核心鉴权检查：没有 API Key 则直接报错。
        if not self.args.ppio_api_key:
            raise RuntimeError("未提供 PPIO API Key，请使用 --ppio-api-key 或设置环境变量 PPIO_API_KEY")

        # 5) 读取题库并做空集校验。
        cases = case_service.load_cases_from_json(cases_file)
        if not cases:
            raise RuntimeError(f"题目文件为空: {cases_file}")

        # 6) 组装运行配置并生成本次运行目录。
        runtime = self.build_runtime()
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = runtime.output_dir / f"{self.args.run_name_prefix}_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)

        # 7) 初始化日志并输出关键上下文。
        logger = setup_logger(run_dir / "run.log")
        logger.info("使用题目文件: %s", cases_file)
        logger.info("题目数量: %d", len(cases))
        logger.info("模型: %s | Base URL: %s", runtime.ppio_model, runtime.ppio_base_url)

        # 8) 调用核心运行器执行全流程。
        summary = PPIOJudgeRunner(runtime, logger).run(cases, default_sampling, run_dir)

        # 9) 返回外层关心的精简摘要。
        return {
            "run_dir": str(run_dir),
            "cases_file": str(cases_file),
            "total_problems": summary.get("total_problems"),
            "hard_problem_count": summary.get("hard_problem_count"),
            "hard_problem_ids": summary.get("hard_problem_ids"),
            "judge_status_summary": summary.get("judge_status_summary", {}),
        }
