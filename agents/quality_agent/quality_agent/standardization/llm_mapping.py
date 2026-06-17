"""大模型辅助字段映射建议。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from ..core.config import LangfuseConfig, ModelConfig
from ..integrations.langfuse import flush_langfuse, get_langfuse_callbacks
from .schema import STANDARD_FIELDS


SYSTEM_PROMPT = """你是 JSON 字段映射助手。
任务：把原始题库 JSON 的字段映射到标准试题字段。
限制：
1. 只能从给定 source_fields 中选择 source。
2. 不要编造不存在的字段。
3. 输出必须是 JSON 对象，不要输出解释文本。
4. 低置信度字段可以不映射。
"""


def _extract_json(text: str) -> Dict[str, Any]:
    """从模型输出中提取 JSON 对象。"""

    raw = str(text or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM mapping result must be a JSON object.")
    return data


def _normalize_llm_mapping(data: Dict[str, Any], source_fields: List[str]) -> Dict[str, Any]:
    """过滤模型输出，确保 source 字段来自原始 JSON。"""

    allowed_sources = set(source_fields)
    standard_names = {field["name"] for field in STANDARD_FIELDS}
    mapping: Dict[str, str] = {}
    confidence: Dict[str, float] = {}

    raw_mapping = data.get("mapping") or data.get("field_mapping") or data
    if not isinstance(raw_mapping, dict):
        return {"mapping": {}, "confidence": {}, "raw": data}

    for target, payload in raw_mapping.items():
        if target not in standard_names:
            continue
        if isinstance(payload, dict):
            source = str(payload.get("source") or "").strip()
            score = payload.get("confidence", 0.75)
        else:
            source = str(payload or "").strip()
            score = 0.75
        if source not in allowed_sources:
            continue
        mapping[target] = source
        try:
            confidence[target] = max(0.0, min(1.0, float(score)))
        except Exception:
            confidence[target] = 0.75
    return {"mapping": mapping, "confidence": confidence, "raw": data}


def guess_mapping_with_llm(
    *,
    source_fields: List[str],
    sample_items: List[Dict[str, Any]],
    model_config: ModelConfig,
    enable_langfuse: bool = False,
    langfuse_config: LangfuseConfig | None = None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """调用 Agent 模型生成字段映射建议。"""

    if not model_config.base_url or not model_config.api_key or not model_config.model_name:
        raise ValueError("Agent 模型配置不完整，无法使用大模型猜测字段映射。")

    from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore
    from langchain_openai import ChatOpenAI  # type: ignore

    schema = [
        {
            "name": field["name"],
            "label": field["label"],
            "required": field["required"],
            "description": field["description"],
        }
        for field in STANDARD_FIELDS
    ]
    payload = {
        "standard_schema": schema,
        "source_fields": source_fields,
        "sample_items": sample_items[:3],
        "required_output": {
            "mapping": {
                "id": {"source": "原始字段路径", "confidence": 0.0},
                "question": {"source": "原始字段路径", "confidence": 0.0},
            }
        },
    }
    llm = ChatOpenAI(
        model=model_config.model_name,
        api_key=model_config.api_key,
        base_url=model_config.base_url,
        temperature=0,
        top_p=1,
        max_tokens=min(max(int(model_config.max_tokens or 1024), 512), 2048),
    )
    trace_metadata = {
        "agent_mode": "field_mapping",
        "source_field_count": len(source_fields),
        **(metadata or {}),
    }
    callbacks = get_langfuse_callbacks(enable_langfuse, trace_metadata, langfuse_config)
    response = llm.invoke(
        [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, indent=2)),
        ],
        config={"callbacks": callbacks, "metadata": trace_metadata},
    )
    flush_langfuse(enable_langfuse, langfuse_config)
    data = _extract_json(str(getattr(response, "content", "")))
    return _normalize_llm_mapping(data, source_fields)
