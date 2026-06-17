"""现有评测工具调用节点。

Agent 不重写已有评测逻辑，而是把 `oj-eval` 当作工具调用：
- math 数据集走客观题评测；
- code 数据集走代码生成 + Judge0 评测。
"""

from __future__ import annotations

from typing import Any, Dict

from ..tools import invoke_oj_eval_tool


def run_eval_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """LangGraph 节点：调用 Solver 工具并把评测摘要写回状态。"""

    config = state["config"]
    dataset_type = state.get("dataset_type", "unknown")
    eval_summary = invoke_oj_eval_tool(
        dataset_path=config.dataset_path,
        dataset_type=dataset_type,
        samples=config.samples,
        output_dir=state["run_dir"] / "solver_runs",
        solver_model=config.solver_model,
        dry_run=config.dry_run,
        enable_langfuse=config.enable_langfuse,
        langfuse_public_key=(config.langfuse.public_key if config.langfuse else ""),
        langfuse_secret_key=(config.langfuse.secret_key if config.langfuse else ""),
        langfuse_base_url=(config.langfuse.base_url if config.langfuse else ""),
        quality_agent_run_id=state["run_dir"].name,
        quality_agent_run_dir=str(state["run_dir"]),
        on_event=state.get("_on_event"),
    )
    errors = list(state.get("errors", []))
    if eval_summary.get("error"):
        errors.append(str(eval_summary["error"]))
    warnings = list(state.get("warnings", []))
    if eval_summary.get("tool_invocation", {}).get("framework") == "direct_fallback":
        warnings.append("oj-eval tool used direct fallback because LangChain StructuredTool was unavailable.")
    langfuse_trace = eval_summary.get("langfuse_trace", {})
    if config.enable_langfuse and langfuse_trace.get("status") not in {"", None, "no_local_error"}:
        warnings.append(str(langfuse_trace.get("message") or "Langfuse 轨迹上传可能失败，请检查配置。"))
    return {**state, "eval_summary": eval_summary, "errors": errors, "warnings": warnings}
