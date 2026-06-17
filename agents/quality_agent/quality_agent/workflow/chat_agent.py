"""对话式质检 Agent。

这个入口面向 UI 的“上传 JSON + 输入指令”场景。它优先使用 LangGraph
ReAct Agent，让大模型决定是否调用质检工具；当模型或工具调用能力不可用时，
退回到固定的 LangGraph 工作流，保证 demo 仍可完成质检。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List

from ..core.config import QualityAgentConfig
from ..integrations.langfuse import flush_langfuse, get_langfuse_callbacks
from .graph import ProgressCallback, run_quality_agent


CHAT_SYSTEM_PROMPT = """你是难题质检智能体。
你可以调用 quality_inspection_tool 对用户上传的 JSON 数据集执行完整性质检、难度评估、
复核队列生成和报告输出。不要编造没有出现在工具结果里的结论。最终用中文回复用户：
1. 本次执行了什么；
2. 数据集类型、题目数量、复核数量；
3. 主要风险和下一步建议；
4. 报告文件路径。
"""

CONVERSATION_SYSTEM_PROMPT = """你是难题质检智能体的对话层。
你需要正常理解用户的多轮话语。用户只是问候、追问上一轮结果、询问能力或配置时，不要声称已经重新运行质检工具。
如果用户明确要求质检、评测、检查数据集、分析难度、生成报告或重新运行，再引导其执行质检工作流。
回答必须使用中文，结论必须基于已知上下文，不要编造工具结果。
"""


def _emit_event(
    callback: ProgressCallback | None,
    *,
    step: str,
    label: str,
    status: str,
    message: str,
) -> None:
    """向 UI 回传对话 Agent 自身的运行事件。"""

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


def _compact_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """把完整 workflow state 压缩成适合对话展示和工具返回的摘要。"""

    config = state["config"]
    quality_results = state.get("quality_results", [])
    review_queue = state.get("review_queue", [])
    eval_summary = state.get("eval_summary", {})
    report_paths = state.get("report_paths", {})
    dry_run_only = bool(quality_results) and all(
        "dry_run_no_solver_evidence" in item.get("risk_flags", []) for item in quality_results
    )
    process_steps = [
        {"step": "读取配置", "status": "完成", "detail": str(config.dataset_path)},
        {"step": "数据集识别", "status": state.get("dataset_type", ""), "detail": f"题目数：{state.get('dataset_summary', {}).get('total_items', 0)}"},
        {"step": "完整性检查", "status": "完成", "detail": f"发现项：{len(state.get('completeness_findings', []))}"},
        {"step": "调用评测工具", "status": eval_summary.get("tool", "oj_eval"), "detail": "Dry run 未调用模型" if eval_summary.get("dry_run") else "已请求评测模型"},
        {"step": "规则评分", "status": "完成", "detail": f"质检结果：{len(quality_results)}"},
        {"step": "Agent 归因", "status": "完成" if state.get("llm_findings") else "未触发或无结果", "detail": f"归因数：{len(state.get('llm_findings', []))}"},
        {"step": "写入报告", "status": "完成" if report_paths else "未完成", "detail": f"输出文件：{len(report_paths)}"},
    ]
    return {
        "run_dir": str(state.get("run_dir", "")),
        "dry_run": bool(config.dry_run),
        "dry_run_only": dry_run_only,
        "process_steps": process_steps,
        "tool_summary": {
            "tool": eval_summary.get("tool", ""),
            "dry_run": eval_summary.get("dry_run", False),
            "returncode": eval_summary.get("returncode", ""),
            "tool_invocation": eval_summary.get("tool_invocation", {}),
            "mode": eval_summary.get("mode", ""),
            "total_problems": eval_summary.get("total_problems", ""),
        },
        "dataset_type": state.get("dataset_type", ""),
        "dataset_summary": state.get("dataset_summary", {}),
        "quality_result_count": len(quality_results),
        "quality_results_preview": quality_results[:5],
        "review_queue_count": len(review_queue),
        "review_queue_preview": review_queue[:5],
        "report_paths": report_paths,
        "warnings": state.get("warnings", []),
        "errors": state.get("errors", []),
    }


def _format_fallback_reply(summary: Dict[str, Any], *, used_react_agent: bool) -> str:
    """生成不依赖模型的中文回复。"""

    dataset_summary = summary.get("dataset_summary", {})
    report_paths = summary.get("report_paths", {})
    warnings = summary.get("warnings", [])
    errors = summary.get("errors", [])
    mode = "LangGraph ReAct Agent" if used_react_agent else "固定 LangGraph 工作流"

    lines = [
        f"已通过{mode}完成本次质检。",
        "",
        f"- 数据集类型：{summary.get('dataset_type') or '未知'}",
        f"- 题目数量：{dataset_summary.get('total_items', 0)}",
        f"- 质检结果数：{summary.get('quality_result_count', 0)}",
        f"- 需要人工复核：{summary.get('review_queue_count', 0)}",
    ]
    if summary.get("dry_run_only"):
        lines.append("- 未评测说明：当前启用了 Dry run，只做结构检查，没有调用评测模型，因此没有正确率证据。关闭 Dry run 后才会真实评测难度。")
    if report_paths:
        lines.append("- 报告文件：")
        for name, path in report_paths.items():
            lines.append(f"  - {name}: `{path}`")
    if warnings:
        lines.append("- 警告：")
        for warning in warnings[:5]:
            lines.append(f"  - {warning}")
    if errors:
        lines.append("- 错误：")
        for error in errors[:5]:
            lines.append(f"  - {error}")
    return "\n".join(lines)


def _format_memory_reply(summary: Dict[str, Any]) -> str:
    """基于上一轮摘要回答追问，避免每轮都强制重跑工作流。"""

    dataset_summary = summary.get("dataset_summary", {})
    report_paths = summary.get("report_paths", {})
    lines = [
        "这是上一轮会话中保留的质检结果摘要：",
        "",
        f"- 数据集类型：{summary.get('dataset_type') or '未知'}",
        f"- 题目数量：{dataset_summary.get('total_items', 0)}",
        f"- 质检结果数：{summary.get('quality_result_count', 0)}",
        f"- 需要人工复核：{summary.get('review_queue_count', 0)}",
    ]
    if report_paths:
        lines.append("- 报告文件：")
        for name, path in report_paths.items():
            lines.append(f"  - {name}: `{path}`")
    if summary.get("warnings"):
        lines.append(f"- 警告数：{len(summary.get('warnings', []))}")
    if summary.get("errors"):
        lines.append(f"- 错误数：{len(summary.get('errors', []))}")
    return "\n".join(lines)


def _format_local_conversation_reply(user_message: str, previous_summary: Dict[str, Any] | None) -> str:
    """在没有可用 Agent 模型时，给普通对话一个不跑工具的本地回复。"""

    text = user_message.strip()
    greeting_keywords = ("你好", "您好", "hello", "hi", "在吗")
    if any(keyword in text.lower() for keyword in greeting_keywords):
        if previous_summary is not None:
            return "你好，我是难题质检智能体。我还保留着上一轮质检结果，可以继续回答报告路径、复核数量、风险原因等追问；如果要重新质检，请明确说“重新跑一次”。"
        return "你好，我是难题质检智能体。你可以让我检查当前 JSON 数据集、评估题目难度、生成复核队列，或询问如何配置模型与 Langfuse。"
    if previous_summary is not None:
        return _format_memory_reply(previous_summary)
    return "我可以先和你确认需求。请告诉我是要执行数据集质检，还是询问当前工具、模型配置、报告结果或运行过程。"


def _conversation_summary(previous_summary: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """构造未调用工具时也能被 UI 安全展示的摘要。"""

    if previous_summary is not None:
        return previous_summary
    return {
        "conversation_only": True,
        "process_steps": [],
        "tool_summary": {},
        "report_paths": {},
        "warnings": [],
        "errors": [],
    }


def _looks_like_new_inspection_request(user_message: str, *, has_previous_summary: bool = False) -> bool:
    """粗略判断本轮是否需要重新执行质检工具。"""

    text = user_message.strip().lower()
    if not text:
        return False
    explicit_memory_keywords = (
        "刚才",
        "上次",
        "上一轮",
        "之前",
        "结果",
        "报告在哪",
        "报告路径",
        "多少",
        "为什么",
        "解释一下",
    )
    explicit_rerun_keywords = ("重新", "再跑", "重跑", "重新质检", "重新检查", "重新评测", "重新运行")
    if has_previous_summary and any(keyword in text for keyword in explicit_memory_keywords) and not any(
        keyword in text for keyword in explicit_rerun_keywords
    ):
        return False
    inspection_keywords = (
        "质检",
        "检查",
        "评测",
        "测评",
        "校验",
        "验证",
        "重新",
        "再跑",
        "重跑",
        "跑一下",
        "运行",
        "执行",
        "分析",
        "分析这份",
        "检查这份",
        "看看这份",
        "看一下这份",
        "处理这份",
        "json",
        "dataset",
        "数据集",
        "题目",
        "难度",
        "正确率",
        "复核建议",
        "生成报告",
    )
    return any(keyword in text for keyword in inspection_keywords)


def _run_quality_workflow_tool(
    *,
    config: QualityAgentConfig,
    prefer_langgraph: bool,
    on_event: ProgressCallback | None = None,
) -> Dict[str, Any]:
    """执行现有质检工作流，并返回压缩摘要。"""

    state = run_quality_agent(config, prefer_langgraph=prefer_langgraph, on_event=on_event)
    return _compact_state(state)


def _build_quality_tool(
    config: QualityAgentConfig,
    prefer_langgraph: bool,
    tool_runtime: Dict[str, Any],
    on_event: ProgressCallback | None = None,
) -> Any:
    """构造 LangChain StructuredTool，供 ReAct Agent 调用。"""

    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class QualityInspectionInput(BaseModel):
        """对话 Agent 调用质检工具时的输入。"""

        reason: str = Field(..., description="Why the agent is running the quality inspection tool.")

    def run_tool(reason: str) -> str:
        _emit_event(
            on_event,
            step="react_tool",
            label="ReAct 工具调用",
            status="running",
            message=f"模型决定调用质检工具：{reason}",
        )
        summary = _run_quality_workflow_tool(config=config, prefer_langgraph=prefer_langgraph, on_event=on_event)
        summary["tool_reason"] = reason
        tool_runtime["summary"] = summary
        _emit_event(
            on_event,
            step="react_tool",
            label="ReAct 工具调用",
            status="completed",
            message="质检工具调用完成，结果已返回给对话 Agent。",
        )
        return json.dumps(summary, ensure_ascii=False, indent=2)

    return StructuredTool.from_function(
        name="quality_inspection_tool",
        description="Run the full quality-inspection workflow on the uploaded JSON dataset.",
        func=run_tool,
        args_schema=QualityInspectionInput,
        return_direct=False,
    )


def _extract_messages_text(messages: List[Any]) -> str:
    """从 LangGraph message 列表中提取最后一条 AI 回复。"""

    for message in reversed(messages):
        content = getattr(message, "content", "")
        if content:
            return str(content)
    return ""


def _history_context(
    chat_history: List[Dict[str, str]] | None,
    previous_summary: Dict[str, Any] | None = None,
) -> str:
    """把 UI 会话历史压缩为模型可读上下文。"""

    lines: List[str] = []
    if previous_summary:
        memory = {
            "dataset_type": previous_summary.get("dataset_type", ""),
            "dataset_summary": previous_summary.get("dataset_summary", {}),
            "quality_result_count": previous_summary.get("quality_result_count", 0),
            "review_queue_count": previous_summary.get("review_queue_count", 0),
            "report_paths": previous_summary.get("report_paths", {}),
            "warnings": previous_summary.get("warnings", []),
            "errors": previous_summary.get("errors", []),
        }
        lines.append("上一轮工具结果摘要如下，回答追问时优先使用这份记忆，不要编造：")
        lines.append(json.dumps(memory, ensure_ascii=False, indent=2)[:3000])
    if not chat_history:
        return "\n".join(lines)
    recent = chat_history[-8:]
    lines.append("以下是本轮之前的会话历史，回答时可参考，但仍以工具结果为准：")
    for item in recent:
        role = item.get("role", "")
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"{role}: {content[:1200]}")
    return "\n".join(lines)


def _build_react_messages(
    *,
    current_message: str,
    chat_history: List[Dict[str, str]] | None,
    memory_context: str,
    human_message_cls: Any,
    ai_message_cls: Any,
) -> List[Any]:
    """构造 ReAct Agent 可直接消费的多轮消息列表。"""

    messages: List[Any] = []
    if memory_context:
        messages.append(human_message_cls(content=memory_context))

    for item in (chat_history or [])[-8:]:
        role = item.get("role", "")
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        if role == "assistant":
            messages.append(ai_message_cls(content=content[:2000]))
        elif role == "user":
            messages.append(human_message_cls(content=content[:2000]))

    messages.append(human_message_cls(content=current_message))
    return messages


def run_chat_quality_agent(
    *,
    user_message: str,
    config: QualityAgentConfig,
    chat_history: List[Dict[str, str]] | None = None,
    previous_summary: Dict[str, Any] | None = None,
    prefer_langgraph: bool = True,
    on_event: ProgressCallback | None = None,
) -> Dict[str, Any]:
    """执行一次对话式 Agent 请求。"""

    config.user_instruction = user_message
    context = _history_context(chat_history, previous_summary)
    llm_user_message = f"{context}\n\n当前用户问题：{user_message}" if context else user_message
    summary: Dict[str, Any] | None = None
    reply = ""
    used_react_agent = False
    tool_was_run = False
    warnings: List[str] = []
    should_run_workflow = _looks_like_new_inspection_request(
        user_message,
        has_previous_summary=previous_summary is not None,
    )

    model_config = config.agent_model
    can_use_react_agent = bool(
        model_config
        and model_config.base_url
        and model_config.api_key
        and model_config.model_name
    )

    _emit_event(
        on_event,
        step="chat_agent",
        label="对话 Agent",
        status="running",
        message="开始解析用户指令并准备调用工具。",
    )

    if not should_run_workflow and not can_use_react_agent:
        summary = _conversation_summary(previous_summary)
        reply = _format_local_conversation_reply(user_message, previous_summary)
        _emit_event(
            on_event,
            step="chat_agent",
            label="对话 Agent",
            status="completed",
            message="已识别为普通对话或结果追问，未运行质检工具。",
        )
        return {
            "reply": reply,
            "summary": summary,
            "used_react_agent": False,
            "tool_was_run": False,
        }

    if not should_run_workflow and can_use_react_agent:
        try:
            from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(
                model=model_config.model_name,
                api_key=model_config.api_key,
                base_url=model_config.base_url,
                temperature=model_config.temperature,
                top_p=model_config.top_p,
                max_tokens=model_config.max_tokens,
                timeout=8,
                max_retries=0,
            )
            messages = [SystemMessage(content=CONVERSATION_SYSTEM_PROMPT)]
            messages.extend(_build_react_messages(
                current_message=user_message,
                chat_history=chat_history,
                memory_context=context,
                human_message_cls=HumanMessage,
                ai_message_cls=AIMessage,
            ))
            callbacks = get_langfuse_callbacks(
                config.enable_langfuse,
                {
                    "dataset_path": str(config.dataset_path),
                    "user_instruction": user_message,
                    "agent_mode": "chat_conversation",
                },
                config.langfuse,
            )
            response = llm.invoke(
                messages,
                config={
                    "callbacks": callbacks,
                    "metadata": {
                        "dataset_path": str(config.dataset_path),
                        "user_instruction": user_message,
                        "agent_mode": "chat_conversation",
                    },
                },
            )
            flush_langfuse(config.enable_langfuse, config.langfuse)
            reply = str(getattr(response, "content", "") or "").strip() or _format_local_conversation_reply(
                user_message,
                previous_summary,
            )
        except Exception as exc:
            warnings.append(f"记忆问答模型调用失败，已使用本地摘要回复：{exc}")
            reply = _format_local_conversation_reply(user_message, previous_summary)
        summary = _conversation_summary(previous_summary)
        if warnings:
            summary["warnings"] = [*summary.get("warnings", []), *warnings]
        _emit_event(
            on_event,
            step="chat_agent",
            label="对话 Agent",
            status="completed",
            message="已由 Agent 模型完成普通对话，未运行质检工具。",
        )
        return {
            "reply": reply,
            "summary": summary,
            "used_react_agent": False,
            "tool_was_run": False,
        }

    if can_use_react_agent:
        try:
            from langchain_core.messages import AIMessage, HumanMessage
            from langchain_openai import ChatOpenAI
            from langgraph.prebuilt import create_react_agent

            llm = ChatOpenAI(
                model=model_config.model_name,
                api_key=model_config.api_key,
                base_url=model_config.base_url,
                temperature=model_config.temperature,
                top_p=model_config.top_p,
                max_tokens=model_config.max_tokens,
            )
            tool_runtime: Dict[str, Any] = {}
            tool = _build_quality_tool(config, prefer_langgraph, tool_runtime, on_event=on_event)
            react_agent = create_react_agent(llm, [tool], prompt=CHAT_SYSTEM_PROMPT)
            callbacks = get_langfuse_callbacks(
                config.enable_langfuse,
                {
                    "dataset_path": str(config.dataset_path),
                    "user_instruction": user_message,
                    "agent_mode": "chat_react",
                },
                config.langfuse,
            )
            react_messages = _build_react_messages(
                current_message=user_message,
                chat_history=chat_history,
                memory_context=context,
                human_message_cls=HumanMessage,
                ai_message_cls=AIMessage,
            )
            response = react_agent.invoke(
                {"messages": react_messages or [HumanMessage(content=llm_user_message)]},
                config={
                    "callbacks": callbacks,
                    "metadata": {
                        "dataset_path": str(config.dataset_path),
                        "user_instruction": user_message,
                        "agent_mode": "chat_react",
                    },
                },
            )
            flush_langfuse(config.enable_langfuse, config.langfuse)
            reply = _extract_messages_text(response.get("messages", []))
            summary = tool_runtime.get("summary")
            tool_was_run = summary is not None
            used_react_agent = True
            _emit_event(
                on_event,
                step="chat_agent",
                label="对话 Agent",
                status="completed",
                message="ReAct Agent 已完成回复生成。",
            )
        except Exception as exc:
            warnings.append(f"ReAct Agent 调用失败，已退回固定工作流：{exc}")
            _emit_event(
                on_event,
                step="chat_agent",
                label="对话 Agent",
                status="failed",
                message=f"ReAct 调用失败，改用固定工作流：{exc}",
            )

    if not used_react_agent:
        _emit_event(
            on_event,
            step="chat_fallback",
            label="固定工作流",
            status="running",
            message="开始执行固定质检工作流。",
        )
        summary = _run_quality_workflow_tool(config=config, prefer_langgraph=prefer_langgraph, on_event=on_event)
        tool_was_run = True
        reply = _format_fallback_reply(summary, used_react_agent=False)
        _emit_event(
            on_event,
            step="chat_fallback",
            label="固定工作流",
            status="completed",
            message="固定质检工作流已完成。",
        )
    elif not summary and should_run_workflow:
        # 如果模型没有实际调用工具，补跑固定工作流，保证用户拿到可验证结果。
        warnings.append("ReAct Agent 未调用质检工具，已补跑固定工作流。")
        _emit_event(
            on_event,
            step="chat_fallback",
            label="补跑固定工作流",
            status="running",
            message="ReAct Agent 未调用工具，开始补跑固定质检工作流。",
        )
        summary = _run_quality_workflow_tool(config=config, prefer_langgraph=prefer_langgraph, on_event=on_event)
        tool_was_run = True
        if not reply:
            reply = _format_fallback_reply(summary, used_react_agent=True)
        _emit_event(
            on_event,
            step="chat_fallback",
            label="补跑固定工作流",
            status="completed",
            message="补跑固定质检工作流已完成。",
        )
    elif not summary and previous_summary is not None:
        summary = previous_summary

    if not reply:
        reply = _format_fallback_reply(summary, used_react_agent=used_react_agent)

    if warnings:
        summary["warnings"] = [*summary.get("warnings", []), *warnings]

    _emit_event(
        on_event,
        step="chat_agent",
        label="对话 Agent",
        status="completed",
        message="本轮对话处理完成。",
    )

    return {
        "reply": reply,
        "summary": summary,
        "used_react_agent": used_react_agent,
        "tool_was_run": tool_was_run,
    }
