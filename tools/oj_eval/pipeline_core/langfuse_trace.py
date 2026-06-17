from __future__ import annotations

"""oj-eval 内部的大模型调用追踪。

oj-eval 的 solver 调用是原始 HTTP 请求，不经过 LangChain callback。
这里用 Langfuse SDK 手动创建 generation，保证 solver 模型调用也能进入
Langfuse，同时不影响原有评测和判题流程。
"""

import os
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator

_CLIENT_CACHE: Dict[str, Any] = {}


def _enabled() -> bool:
    """判断当前子进程是否启用 Langfuse 追踪。"""

    return os.getenv("QUALITY_AGENT_LANGFUSE_ENABLED", "").lower() in {"1", "true", "yes", "on"}


def _safe_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """复制请求载荷，保留模型输入但不包含任何认证信息。"""

    return {
        "model": payload.get("model"),
        "messages": payload.get("messages", []),
        "temperature": payload.get("temperature"),
        "top_p": payload.get("top_p"),
        "frequency_penalty": payload.get("frequency_penalty"),
        "max_tokens": payload.get("max_tokens"),
        "top_k": payload.get("top_k"),
        "stream": payload.get("stream"),
    }


def _response_output(data: Dict[str, Any]) -> Any:
    """提取适合写入 Langfuse 的模型输出。"""

    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and choices:
        message = (choices[0] or {}).get("message") or {}
        return message.get("content") or message
    if "_raw_text" in data:
        return data.get("_raw_text")
    if "error" in data:
        return {"error": data.get("error")}
    return data


def _usage_details(data: Dict[str, Any]) -> Dict[str, int]:
    """兼容 OpenAI 风格 usage 字段。"""

    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return {}
    result: Dict[str, int] = {}
    mapping = {
        "prompt_tokens": "input",
        "completion_tokens": "output",
        "total_tokens": "total",
    }
    for source_key, target_key in mapping.items():
        value = usage.get(source_key)
        if isinstance(value, int):
            result[target_key] = value
    return result


def _metadata(extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """生成通用追踪元数据。"""

    metadata: Dict[str, Any] = {
        "source": "oj_eval",
        "quality_agent_run_id": os.getenv("QUALITY_AGENT_RUN_ID", ""),
        "quality_agent_run_dir": os.getenv("QUALITY_AGENT_RUN_DIR", ""),
        "dataset_path": os.getenv("QUALITY_AGENT_DATASET_PATH", ""),
        "dataset_type": os.getenv("QUALITY_AGENT_DATASET_TYPE", ""),
    }
    if extra:
        metadata.update(extra)
    return metadata


def _client() -> Any | None:
    """按环境变量初始化 Langfuse 客户端。"""

    if not _enabled():
        return None
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    base_url = os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST", "")
    if not public_key or not secret_key:
        return None
    cache_key = f"{base_url}|{public_key}"
    cached = _CLIENT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        from langfuse import Langfuse  # type: ignore

        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url or None,
        )
        _CLIENT_CACHE[cache_key] = client
        return client
    except Exception:
        return None


@contextmanager
def trace_generation(
    *,
    name: str,
    payload: Dict[str, Any],
    metadata: Dict[str, Any] | None = None,
) -> Iterator[Any | None]:
    """为一次原始 HTTP 大模型调用创建 Langfuse generation。"""

    client = _client()
    generation = None
    if client is not None:
        try:
            generation = client.start_observation(
                name=name,
                as_type="generation",
                input=_safe_payload(payload),
                model=str(payload.get("model") or ""),
                model_parameters={
                    "temperature": payload.get("temperature"),
                    "top_p": payload.get("top_p"),
                    "frequency_penalty": payload.get("frequency_penalty"),
                    "max_tokens": payload.get("max_tokens"),
                    "top_k": payload.get("top_k"),
                },
                metadata=_metadata(metadata),
                completion_start_time=datetime.now(),
            )
        except Exception:
            generation = None

    try:
        yield generation
    except Exception as exc:
        if generation is not None:
            try:
                generation.update(level="ERROR", status_message=str(exc))
                generation.end()
                client = _client()
                if client is not None:
                    client.flush()
            except Exception:
                pass
        raise


def finish_generation(generation: Any | None, response_data: Dict[str, Any]) -> None:
    """用模型响应结束 generation，并刷新 Langfuse 队列。"""

    if generation is None:
        return
    try:
        generation.update(
            output=_response_output(response_data),
            usage_details=_usage_details(response_data),
        )
        generation.end()
        client = _client()
        if client is not None:
            client.flush()
    except Exception:
        return


def finish_generation_error(
    generation: Any | None,
    *,
    message: str,
    output: Any | None = None,
    level: str = "WARNING",
) -> None:
    """结束失败或被重试的 generation。"""

    if generation is None:
        return
    try:
        generation.update(
            output=output if output is not None else {"error": message},
            level=level,
            status_message=message,
        )
        generation.end()
        client = _client()
        if client is not None:
            client.flush()
    except Exception:
        return
