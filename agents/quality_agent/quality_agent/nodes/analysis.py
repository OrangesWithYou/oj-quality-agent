"""Agent 模型归因分析节点。

该模块使用 LangChain 调用 Agent 模型，只分析已经由规则层标出的风险题。
模型输出用于解释和复核建议，不直接覆盖规则评分。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from ..integrations.langfuse import flush_langfuse, get_langfuse_callbacks, langfuse_metadata


SYSTEM_PROMPT = (
    "You are a dataset quality-inspection agent. "
    "Only use the provided evidence. Do not invent hidden tests, human reviews, "
    "or facts that are not present in the input."
)


def _build_prompt(result: Dict[str, Any]) -> str:
    """把单题质检证据压缩成 Agent 模型可分析的 Prompt。"""

    payload = {
        "problem_id": result.get("problem_id"),
        "question_type": result.get("question_type"),
        "sample_pass_rate": result.get("sample_pass_rate"),
        "agent_difficulty": result.get("agent_difficulty"),
        "risk_flags": result.get("risk_flags"),
        "completeness_findings": result.get("completeness_findings"),
        "evidence": result.get("evidence"),
    }
    return (
        "Analyze this quality-inspection result and return concise JSON with keys "
        "reason, risk_reason, suggestion, confidence. Evidence:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def run_llm_analysis_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """LangGraph 节点：可选调用 Agent 模型做归因解释。"""

    config = state["config"]
    if not config.enable_llm_analysis:
        # 未开启时保持空结果，后续报告逻辑不用分支处理。
        return {**state, "llm_findings": []}

    model_config = config.agent_model
    if model_config is None or not model_config.api_key or not model_config.base_url or not model_config.model_name:
        # Agent 模型配置不完整时只记 warning，不中断整条质检链路。
        warnings = list(state.get("warnings", []))
        warnings.append("LLM analysis skipped: agent model base_url/api_key/model_name is incomplete.")
        return {**state, "llm_findings": [], "warnings": warnings}

    try:
        # LangChain 是可选依赖；只有开启 Agent 分析时才需要导入。
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore
        from langchain_openai import ChatOpenAI  # type: ignore
    except Exception:
        warnings = list(state.get("warnings", []))
        warnings.append("LLM analysis skipped: install optional agent dependencies with pip install -e .[agent].")
        return {**state, "llm_findings": [], "warnings": warnings}

    callbacks = get_langfuse_callbacks(config.enable_langfuse, langfuse_metadata(state), config.langfuse)
    # ChatOpenAI 支持 OpenAI-compatible base_url，便于切换 PPIO、本地模型或云端模型。
    llm = ChatOpenAI(
        model=model_config.model_name,
        api_key=model_config.api_key,
        base_url=model_config.base_url,
        temperature=model_config.temperature,
        top_p=model_config.top_p,
        max_tokens=model_config.max_tokens,
    )

    findings: List[Dict[str, Any]] = []
    warnings = list(state.get("warnings", []))
    for result in state.get("quality_results", []):
        # 只分析需要复核的题，控制成本，也避免报告被冗余解释淹没。
        if not result.get("need_human_review"):
            continue
        try:
            response = llm.invoke(
                [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=_build_prompt(result))],
                config={"callbacks": callbacks, "metadata": {"problem_id": result.get("problem_id")}},
            )
            content = getattr(response, "content", "")
            finding = {
                "problem_id": result.get("problem_id"),
                "raw_analysis": content,
            }
            try:
                # 若模型严格返回 JSON，则展开成结构化字段；否则保留 raw_analysis。
                parsed = json.loads(str(content))
                if isinstance(parsed, dict):
                    finding.update(parsed)
            except Exception:
                pass
            findings.append(finding)
        except Exception as exc:
            warnings.append(f"LLM analysis failed for {result.get('problem_id')}: {exc}")

    flush_langfuse(config.enable_langfuse, config.langfuse)
    return {**state, "llm_findings": findings, "warnings": warnings}
