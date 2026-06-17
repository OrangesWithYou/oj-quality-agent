"""命令行入口。

该入口面向脚本化调用和调试，功能与 Streamlit 页面对应：
收集参数 -> 组装 QualityAgentConfig -> 调用工作流 -> 输出摘要 JSON。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .core.config import LangfuseConfig, ModelConfig, QualityAgentConfig
from .workflow.graph import run_quality_agent


def _add_model_args(parser: argparse.ArgumentParser, prefix: str, label: str) -> None:
    """给指定模型角色批量注册 OpenAI 兼容参数。

    prefix 用来区分 solver / agent 两个模型槽位。
    """

    parser.add_argument(f"--{prefix}-base-url", default="", help=f"{label} OpenAI-compatible base URL")
    parser.add_argument(f"--{prefix}-api-key", default="", help=f"{label} API key")
    parser.add_argument(f"--{prefix}-model", default="", help=f"{label} model name")
    parser.add_argument(f"--{prefix}-temperature", type=float, default=0.1, help=f"{label} temperature")
    parser.add_argument(f"--{prefix}-top-p", type=float, default=0.95, help=f"{label} top_p")
    parser.add_argument(f"--{prefix}-max-tokens", type=int, default=1024, help=f"{label} max_tokens")


def _model_config(args: argparse.Namespace, prefix: str) -> ModelConfig:
    """从 argparse 结果中提取某个模型角色的配置。"""

    return ModelConfig(
        base_url=getattr(args, f"{prefix}_base_url"),
        api_key=getattr(args, f"{prefix}_api_key"),
        model_name=getattr(args, f"{prefix}_model"),
        temperature=getattr(args, f"{prefix}_temperature"),
        top_p=getattr(args, f"{prefix}_top_p"),
        max_tokens=getattr(args, f"{prefix}_max_tokens"),
    )


def _langfuse_config(args: argparse.Namespace) -> LangfuseConfig:
    """从 argparse 结果中提取 Langfuse 配置。"""

    return LangfuseConfig(
        public_key=args.langfuse_public_key,
        secret_key=args.langfuse_secret_key,
        base_url=args.langfuse_base_url,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """构建 quality-agent 命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="Quality-agent workflow for OJ/code and objective-question datasets.")
    subparsers = parser.add_subparsers(dest="command")

    # 当前只实现 run 子命令，后续可以继续扩展 inspect / report / serve。
    run_parser = subparsers.add_parser("run", help="Run one quality-agent inspection.")
    run_parser.add_argument("--dataset", required=True, help="Dataset JSON file.")
    run_parser.add_argument("--instruction", default="", help="Optional user instruction recorded in reports and traces.")
    run_parser.add_argument("--mode", choices=["auto", "code", "math", "mixed"], default="auto", help="Dataset mode.")
    run_parser.add_argument("--samples", type=int, default=4, help="Samples per item for the solver model.")
    run_parser.add_argument("--output-dir", default="runs/quality_agent", help="Quality-agent output directory.")
    run_parser.add_argument("--dry-run", action="store_true", help="Inspect and report without calling solver model tools.")
    run_parser.add_argument("--enable-llm-analysis", action="store_true", help="Use the agent model to explain risky items.")
    run_parser.add_argument("--enable-langfuse", action="store_true", help="Attach Langfuse callbacks to LLM calls when configured.")
    run_parser.add_argument("--langfuse-public-key", default="", help="Langfuse public key.")
    run_parser.add_argument("--langfuse-secret-key", default="", help="Langfuse secret key.")
    run_parser.add_argument("--langfuse-base-url", default="", help="Langfuse base URL, for example https://cloud.langfuse.com.")
    run_parser.add_argument("--no-langgraph", action="store_true", help="Use the sequential fallback instead of LangGraph.")
    _add_model_args(run_parser, "solver", "Solver model")
    _add_model_args(run_parser, "agent", "Agent model")

    return parser


def _run(args: argparse.Namespace) -> dict[str, Any]:
    """执行一次质检任务，并返回适合打印到终端的精简摘要。"""

    config = QualityAgentConfig(
        dataset_path=Path(args.dataset),
        user_instruction=args.instruction,
        mode=args.mode,
        samples=args.samples,
        output_dir=Path(args.output_dir),
        dry_run=args.dry_run,
        enable_llm_analysis=args.enable_llm_analysis,
        enable_langfuse=args.enable_langfuse,
        solver_model=_model_config(args, "solver"),
        agent_model=_model_config(args, "agent"),
        langfuse=_langfuse_config(args),
    )
    state = run_quality_agent(config, prefer_langgraph=not args.no_langgraph)
    return {
        "run_dir": str(state.get("run_dir", "")),
        "dataset_type": state.get("dataset_type", ""),
        "quality_result_count": len(state.get("quality_results", [])),
        "review_queue_count": len(state.get("review_queue", [])),
        "report_paths": state.get("report_paths", {}),
        "warnings": state.get("warnings", []),
        "errors": state.get("errors", []),
    }


def main() -> None:
    """CLI 主入口。"""

    parser = build_arg_parser()
    args = parser.parse_args()
    if args.command == "run":
        print(json.dumps(_run(args), ensure_ascii=False, indent=2))
        return
    parser.print_help()


if __name__ == "__main__":
    main()
