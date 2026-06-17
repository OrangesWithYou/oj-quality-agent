"""工作流编排层。

这里是 Agent 的“骨架”：用 LangGraph 声明各节点的执行顺序；
当环境未安装 LangGraph 时，使用同样节点的顺序执行兜底。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict

from ..core.config import QualityAgentConfig
from ..core.state import QualityState
from ..integrations.langfuse import langfuse_metadata
from ..nodes.analysis import run_llm_analysis_node
from ..nodes.evaluation import run_eval_node
from ..nodes.inspection import inspect_dataset_node
from ..nodes.reporting import write_report_node
from ..nodes.scoring import score_quality_node

ProgressCallback = Callable[[Dict[str, Any]], None]


def _emit_progress(
    callback: ProgressCallback | None,
    *,
    step: str,
    label: str,
    status: str,
    message: str,
) -> None:
    """向 UI 或调用方发送实时运行事件。"""

    if callback is None:
        return
    callback(
        {
            "time": datetime.now().strftime("%H:%M:%S"),
            "step": step,
            "label": label,
            "status": status,
            "message": message,
        }
    )


def _describe_step_result(step: str, state: Dict[str, Any]) -> str:
    """把节点输出压缩成适合实时日志展示的一句话。"""

    if step == "inspect_dataset":
        summary = state.get("dataset_summary", {})
        return f"识别为 {state.get('dataset_type', 'unknown')}，题目数 {summary.get('total_items', 0)}。"
    if step == "finalize_trace_metadata":
        return "追踪元数据已生成。"
    if step == "run_eval":
        eval_summary = state.get("eval_summary", {})
        if eval_summary.get("dry_run"):
            return "Dry run 模式，跳过评测模型调用。"
        return f"评测工具返回：{eval_summary.get('tool', 'oj_eval')}，returncode={eval_summary.get('returncode', 'n/a')}。"
    if step == "score_quality":
        return f"已生成 {len(state.get('quality_results', []))} 条质检结果。"
    if step == "llm_analysis":
        return f"已生成 {len(state.get('llm_findings', []))} 条 Agent 归因。"
    if step == "write_report":
        return f"报告已写入 {state.get('run_dir', '')}。"
    return "完成。"


def _run_step(
    current: Dict[str, Any],
    *,
    step: str,
    label: str,
    node: Callable[[Dict[str, Any]], Dict[str, Any]],
    on_event: ProgressCallback | None,
) -> Dict[str, Any]:
    """执行一个工作流节点，并在开始、完成、失败时发送事件。"""

    _emit_progress(on_event, step=step, label=label, status="running", message="开始执行。")
    try:
        node_input = {**current, "_on_event": on_event}
        updated = node(node_input)
        updated.pop("_on_event", None)
    except Exception as exc:
        _emit_progress(on_event, step=step, label=label, status="failed", message=str(exc))
        raise
    _emit_progress(
        on_event,
        step=step,
        label=label,
        status="completed",
        message=_describe_step_result(step, updated),
    )
    return updated


def _initialize_state(config: QualityAgentConfig) -> QualityState:
    """创建初始状态和本次运行目录。"""

    config = config.normalized()
    # 使用微秒级时间戳，避免连续点击或并发运行时目录冲突。
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = config.output_dir / f"run_quality_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    state: QualityState = {
        "config": config,
        "run_dir": run_dir,
        "errors": [],
        "warnings": [],
        "trace_metadata": {},
    }
    return state


def _finalize_trace_metadata(state: Dict[str, Any]) -> Dict[str, Any]:
    """在数据集识别后补齐 Langfuse 等追踪系统需要的元数据。"""

    return {**state, "trace_metadata": langfuse_metadata(state)}


def _has_review_candidates(state: Dict[str, Any]) -> bool:
    """判断当前结果中是否存在值得 Agent 模型解释的复核项。"""

    return any(item.get("need_human_review") for item in state.get("quality_results", []))


def _route_after_scoring(state: Dict[str, Any]) -> str:
    """LangGraph 条件路由：决定是否进入 LLM 归因节点。"""

    config = state["config"]
    if config.enable_llm_analysis and _has_review_candidates(state):
        return "llm_analysis"
    return "write_report"


def _run_sequential(state: QualityState, *, on_event: ProgressCallback | None = None) -> QualityState:
    """未安装 LangGraph 时的顺序执行路径。

    这让 UI 和 CLI 在最小依赖环境下也能跑通结构检查和报告生成。
    """

    current: Dict[str, Any] = dict(state)
    current = _run_step(current, step="inspect_dataset", label="数据集识别", node=inspect_dataset_node, on_event=on_event)
    current = _run_step(current, step="finalize_trace_metadata", label="生成追踪元数据", node=_finalize_trace_metadata, on_event=on_event)
    current = _run_step(current, step="run_eval", label="调用评测工具", node=run_eval_node, on_event=on_event)
    current = _run_step(current, step="score_quality", label="规则评分", node=score_quality_node, on_event=on_event)
    if state["config"].enable_llm_analysis and _has_review_candidates(current):
        current = _run_step(current, step="llm_analysis", label="Agent 归因分析", node=run_llm_analysis_node, on_event=on_event)
    else:
        _emit_progress(
            on_event,
            step="llm_analysis",
            label="Agent 归因分析",
            status="skipped",
            message="未开启 Agent 分析，或没有需要复核的题目。",
        )
    current = _run_step(current, step="write_report", label="写入报告", node=write_report_node, on_event=on_event)
    return current  # type: ignore[return-value]


def build_graph() -> Any:
    """构建 LangGraph 工作流。"""

    try:
        from langgraph.graph import END, START, StateGraph  # type: ignore
    except Exception as exc:
        raise RuntimeError("LangGraph is not installed. Install with: pip install -e .[agent]") from exc

    graph = StateGraph(QualityState)

    # 每个 node 都是一个纯函数式处理单元：输入 state，返回更新后的 state。
    graph.add_node("inspect_dataset", inspect_dataset_node)
    graph.add_node("finalize_trace_metadata", _finalize_trace_metadata)
    graph.add_node("run_eval", run_eval_node)
    graph.add_node("score_quality", score_quality_node)
    graph.add_node("llm_analysis", run_llm_analysis_node)
    graph.add_node("write_report", write_report_node)

    # LangGraph 在评分后做条件路由：只有开启 LLM 分析且存在复核项时才进入模型归因。
    graph.add_edge(START, "inspect_dataset")
    graph.add_edge("inspect_dataset", "finalize_trace_metadata")
    graph.add_edge("finalize_trace_metadata", "run_eval")
    graph.add_edge("run_eval", "score_quality")
    graph.add_conditional_edges(
        "score_quality",
        _route_after_scoring,
        {
            "llm_analysis": "llm_analysis",
            "write_report": "write_report",
        },
    )
    graph.add_edge("llm_analysis", "write_report")
    graph.add_edge("write_report", END)
    return graph.compile()


def run_quality_agent(
    config: QualityAgentConfig,
    *,
    prefer_langgraph: bool = True,
    on_event: ProgressCallback | None = None,
) -> QualityState:
    """执行一次质检 Agent 工作流。"""

    state = _initialize_state(config)
    _emit_progress(
        on_event,
        step="initialize",
        label="初始化运行",
        status="completed",
        message=f"运行目录：{state['run_dir']}",
    )
    if on_event is not None:
        _emit_progress(
            on_event,
            step="workflow",
            label="工作流模式",
            status="running",
            message="实时日志模式按 LangGraph 节点顺序逐步执行。",
        )
        return _run_sequential(state, on_event=on_event)
    if prefer_langgraph:
        try:
            graph = build_graph()
            return graph.invoke(state)
        except RuntimeError:
            # 没装 LangGraph 时降级，不影响基础功能体验。
            warnings = list(state.get("warnings", []))
            warnings.append("LangGraph unavailable; falling back to sequential workflow.")
            state["warnings"] = warnings
    return _run_sequential(state)
