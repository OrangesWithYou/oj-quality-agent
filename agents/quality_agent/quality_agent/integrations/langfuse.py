"""Langfuse 追踪封装。

这里不直接依赖 Langfuse 必须存在；未安装或未配置密钥时返回空 callback，
主流程仍可正常运行。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from ..core.config import LangfuseConfig

_CLIENT_CACHE: Dict[str, Any] = {}


def _client_cache_key(config: LangfuseConfig) -> str:
    """用非敏感字段区分不同 Langfuse 项目客户端。"""

    return f"{config.base_url}|{config.public_key}"


def _ensure_langfuse_client(config: LangfuseConfig) -> Any | None:
    """初始化并缓存 Langfuse 客户端。

    Langfuse v4 的 CallbackHandler(public_key=...) 会调用 get_client(public_key=...)。
    如果之前没有显式初始化过同 public key 的客户端，SDK 会返回 disabled client，
    导致页面里看不到 trace。
    """

    if not config.is_complete():
        return None
    cache_key = _client_cache_key(config)
    cached = _CLIENT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    config.apply_to_env()
    try:
        from langfuse import Langfuse  # type: ignore
    except Exception:
        return None

    try:
        client = Langfuse(
            public_key=config.public_key,
            secret_key=config.secret_key,
            base_url=config.base_url or None,
        )
    except Exception:
        return None
    _CLIENT_CACHE[cache_key] = client
    return client


def langfuse_metadata(state: Dict[str, Any]) -> Dict[str, Any]:
    """从工作流状态提取写入 Langfuse trace 的元数据。"""

    config = state["config"]
    return {
        "dataset_path": str(config.dataset_path),
        "user_instruction": config.user_instruction,
        "dataset_type": state.get("dataset_type", ""),
        "samples": config.samples,
        "solver_model": (config.solver_model.model_name if config.solver_model else ""),
        "agent_model": (config.agent_model.model_name if config.agent_model else ""),
        "langfuse_base_url": (config.langfuse.base_url if config.langfuse else ""),
        "run_dir": str(state.get("run_dir", "")),
    }


def get_langfuse_callbacks(
    enabled: bool,
    metadata: Dict[str, Any] | None = None,
    langfuse_config: LangfuseConfig | None = None,
) -> List[Any]:
    """按需创建 Langfuse callback。

    返回 List 是为了直接传给 LangChain 的 config={"callbacks": callbacks}。
    """

    if not enabled:
        return []
    config = (langfuse_config or LangfuseConfig.from_env()).normalized()
    if not config.is_complete():
        # 没配密钥时静默降级，避免用户只想本地跑 demo 时被阻塞。
        return []
    if _ensure_langfuse_client(config) is None:
        return []
    try:
        # langfuse 是可选依赖，只在需要时导入。
        from langfuse.langchain import CallbackHandler  # type: ignore
    except Exception:
        return []

    try:
        handler = CallbackHandler(public_key=config.public_key)
    except TypeError:
        handler = CallbackHandler()
    return [handler]


def flush_langfuse(enabled: bool, langfuse_config: LangfuseConfig | None = None) -> None:
    """主动刷新 Langfuse 事件，避免短任务结束后后台队列还没上传。"""

    if not enabled:
        return
    config = (langfuse_config or LangfuseConfig.from_env()).normalized()
    if not config.is_complete():
        return
    client = _ensure_langfuse_client(config)
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        # 追踪失败不应影响质检主流程；UI 会通过说明提醒用户检查配置和触发条件。
        return
