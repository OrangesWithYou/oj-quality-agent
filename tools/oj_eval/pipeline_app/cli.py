from __future__ import annotations

# CLI 模块：负责参数定义、参数解析、调用应用编排层。
import argparse
import json
import os
from typing import Any, Dict

from pipeline_core import (
    JUDGE0_BASE_URL,
    JUDGE0_LANGUAGE_ID,
    PPIO_API_KEY,
    PPIO_BASE_URL,
    PPIO_MODEL,
)

from .application import PipelineApplication
from .constants import (
    DEFAULT_CASES_FILE,
    DEFAULT_MATH_CASES_FILE,
    DEFAULT_MATH_QUESTION_TYPES,
    DEFAULT_MATH_QUIZ_OUTPUT_DIR,
    DEFAULT_MATH_QUIZ_RUN_NAME_PREFIX,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RUN_NAME_PREFIX,
)


def build_arg_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""

    # 入口描述：统一覆盖 JSON / Excel / demo 三种题目来源。
    parser = argparse.ArgumentParser(description="统一入口：JSON/Excel/demo 题目都可直接运行")

    # ========== 业务域与目录初始化 ==========
    parser.add_argument(
        "--domain",
        type=str,
        choices=["general", "code", "math"],
        default="general",
        help="业务域隔离：code/math 会自动使用各自默认 cases 与 runs 目录（若你未手动指定）",
    )
    # 一次性初始化 code/math 的目录与空题库模板。
    parser.add_argument("--init-domain-layout", action="store_true", help="初始化 code/math 隔离目录并退出")

    # ========== 题目来源相关 ==========
    # JSON 题库路径。
    parser.add_argument("--cases-file", "--code", dest="cases_file", type=str, default=DEFAULT_CASES_FILE, help="代码题题目文件（JSON），--code 为别名")
    # 使用内置 demo 题库。
    parser.add_argument("--use-demo-cases", action="store_true", help="启用内置 demo 题库模式")
    # 与 demo 配合：重写题库文件。
    parser.add_argument("--regen-cases", action="store_true", help="与 --use-demo-cases 配合，重写 cases 文件")
    # Excel -> JSON 导出模式。
    parser.add_argument("--export-first-from-excel", type=str, default="", help="从 Excel 导出第一题到 cases 文件后退出")

    # ========== 数学客观题测验（隔离模式） ==========
    parser.add_argument(
        "--run-math-quiz", "--math",
        action="store_true",
        # 总开关：开启后走“数学客观题测验”独立流程，不再走代码生成+Judge0流程。
        help="启用数学客观题测验模式（与代码生成+Judge流程隔离），--math 为别名",
    )
    parser.add_argument(
        "--math-cases-file",
        type=str,
        default=DEFAULT_MATH_CASES_FILE,
        # 数学题库 JSON 路径，默认读取项目根目录下的 cases_math.json。
        help="数学客观题题库文件（JSON）",
    )
    parser.add_argument(
        "--math-question-types",
        type=str,
        default=DEFAULT_MATH_QUESTION_TYPES,
        # 指定要测哪些题型：可组合（逗号/分号/竖线分隔），如 判断,单选,多选。
        help="数学客观题题型过滤，逗号分隔（如：判断,单选,多选）",
    )
    # 本次运行最多测多少题：0 代表不限制（即测完过滤后的全部题）。
    parser.add_argument("--math-max-cases", type=int, default=0, help="数学测验最大题数，0 表示不限制")
    parser.add_argument(
        "--math-quiz-output-dir",
        type=str,
        default=DEFAULT_MATH_QUIZ_OUTPUT_DIR,
        # 数学测验输出根目录：会在该目录下创建 run_xxx 子目录。
        help="数学测验输出目录",
    )
    parser.add_argument(
        "--math-quiz-run-name-prefix",
        type=str,
        default=DEFAULT_MATH_QUIZ_RUN_NAME_PREFIX,
        # 本次运行目录名前缀，最终目录形如 run_math_quiz_YYYYMMDD_HHMMSS。
        help="数学测验运行目录前缀",
    )
    # 数学测验调用模型的采样参数（越低通常越稳定；客观题建议低温）。
    parser.add_argument("--math-temperature", type=float, default=0.2, help="数学测验温度参数")
    parser.add_argument("--math-top-p", type=float, default=0.95, help="数学测验 top_p 参数")
    parser.add_argument("--math-max-tokens", type=int, default=256, help="数学测验 max_tokens 参数")
    parser.add_argument("--math-samples", type=int, default=4, help="数学测验每题独立采样次数（默认 4）")
    parser.add_argument(
        "--math-ids",
        type=str,
        default="",
        help="按题目 ID 抽样，逗号分隔（如 1,39,131）；指定后忽略 --math-question-types 和 --math-max-cases",
    )
    parser.add_argument(
        "--math-request-interval",
        type=float,
        default=0.5,
        help="数学测验每次 API 请求后的等待秒数，防止限流（默认 0.5，设为 0 禁用）",
    )

    # ========== 采样参数 ==========
    parser.add_argument("--samples", type=int, default=4, help="每题独立采样次数")
    parser.add_argument("--temperature", type=float, default=0.7, help="默认 temperature")
    parser.add_argument("--top-p", type=float, default=0.95, help="默认 top_p")
    parser.add_argument("--frequency-penalty", type=float, default=0.0, help="默认 frequency_penalty")
    parser.add_argument("--max-tokens", type=int, default=1024, help="默认 max_tokens")
    parser.add_argument("--top-k", type=int, default=64, help="默认 top_k（接口支持时生效）")

    # ========== PPIO 接口相关 ==========
    parser.add_argument("--model", type=str, default=os.getenv("PPIO_MODEL", PPIO_MODEL), help="模型名")
    parser.add_argument("--ppio-api-key", type=str, default=os.getenv("PPIO_API_KEY", PPIO_API_KEY), help="PPIO API Key")
    parser.add_argument("--ppio-base-url", type=str, default=os.getenv("PPIO_BASE_URL", PPIO_BASE_URL), help="PPIO Base URL")

    # ========== Judge0 接口相关 ==========
    parser.add_argument("--judge0-base-url", type=str, default=os.getenv("JUDGE0_BASE_URL", JUDGE0_BASE_URL), help="Judge0 Base URL")
    parser.add_argument("--language-id", type=int, default=int(os.getenv("JUDGE0_LANGUAGE_ID", str(JUDGE0_LANGUAGE_ID))), help="Judge0 language_id")

    # ========== 运行控制相关 ==========
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Judge 轮询间隔秒")
    parser.add_argument("--poll-timeout", type=int, default=300, help="Judge 轮询超时秒")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR, help="输出目录")
    parser.add_argument("--run-name-prefix", type=str, default=DEFAULT_RUN_NAME_PREFIX, help="运行目录前缀")
    parser.add_argument(
        "--final-pick-strategy",
        type=str,
        choices=["last", "best_pass"],
        default="best_pass",
        help="最终代码选择策略：last=最后一次采样, best_pass=首个通过样本",
    )

    # 返回可复用 parser（供 main 和外部脚本共用）。
    return parser


def run_pipeline(args: argparse.Namespace) -> Dict[str, Any]:
    """提供给其他脚本复用的统一执行接口。"""

    # 将实际执行委托给应用编排层。
    return PipelineApplication(args).run()


def main() -> None:
    """命令行主入口：解析参数并输出 JSON 结果。"""

    # 1) 解析参数。
    args = build_arg_parser().parse_args()
    # 2) 执行流程。
    result = run_pipeline(args)
    # 3) 结构化打印，便于日志记录与脚本消费。
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    # 允许 `python -m pipeline_app.cli` 直接运行。
    main()
