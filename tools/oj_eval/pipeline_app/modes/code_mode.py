from __future__ import annotations

"""CodeMode：代码生成 + Judge0 判题流程。"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from pipeline_core import PPIOJudgeRunner, RuntimeConfig, SamplingConfig, setup_logger

from ..case_service import CaseService
from ..constants import DEFAULT_RUN_NAME_PREFIX, DOMAIN_PRESETS
from .base import BaseMode


class CodeMode(BaseMode):
    """代码生成评测模式。"""

    name = "code"

    def run(self) -> Dict[str, Any]:
        args = self.args

        if not args.ppio_api_key:
            raise RuntimeError("未提供 PPIO API Key，请使用 --ppio-api-key 或设置环境变量 PPIO_API_KEY")

        default_sampling = SamplingConfig(
            temperature=args.temperature,
            top_p=args.top_p,
            frequency_penalty=args.frequency_penalty,
            max_tokens=args.max_tokens,
            top_k=args.top_k,
        )
        case_service = CaseService(default_sampling=default_sampling, samples_per_problem=args.samples)

        if args.regen_cases and not args.use_demo_cases:
            raise ValueError("--regen-cases 需要和 --use-demo-cases 一起使用")

        cases_file = Path(args.cases_file)
        if args.use_demo_cases:
            case_service.ensure_demo_cases_file(cases_file, regen=args.regen_cases)

        cases = case_service.load_cases_from_json(cases_file)
        if not cases:
            raise RuntimeError(f"题目文件为空: {cases_file}")

        runtime = RuntimeConfig(
            ppio_api_key=args.ppio_api_key,
            ppio_base_url=args.ppio_base_url,
            ppio_model=args.model,
            judge0_base_url=args.judge0_base_url,
            judge0_language_id=args.language_id,
            samples_per_problem=args.samples,
            poll_interval_sec=args.poll_interval,
            poll_timeout_sec=args.poll_timeout,
            output_dir=Path(args.output_dir),
            final_pick_strategy=args.final_pick_strategy,
        )

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name_prefix = getattr(args, "run_name_prefix", DEFAULT_RUN_NAME_PREFIX)
        run_dir = runtime.output_dir / f"{run_name_prefix}_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)

        logger = setup_logger(run_dir / "run.log")
        logger.info("使用题目文件: %s", cases_file)
        logger.info("题目数量: %d", len(cases))
        logger.info("模型: %s | Base URL: %s", runtime.ppio_model, runtime.ppio_base_url)

        summary = PPIOJudgeRunner(runtime, logger).run(cases, default_sampling, run_dir)

        return {
            "mode": "code",
            "run_dir": str(run_dir),
            "cases_file": str(cases_file),
            "total_problems": summary.get("total_problems"),
            "hard_problem_count": summary.get("hard_problem_count"),
            "hard_problem_ids": summary.get("hard_problem_ids"),
            "judge_status_summary": summary.get("judge_status_summary", {}),
        }
