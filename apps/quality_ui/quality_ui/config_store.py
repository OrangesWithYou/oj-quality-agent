"""本地 UI 配置记忆。

这个模块只服务 Streamlit 界面，用 JSON 保存用户上次输入的路径、模型地址、
模型名和开关状态。Agent 工作流仍然只接收一次性的 QualityAgentConfig。
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[3]
UI_CONFIG_FILE = PROJECT_ROOT / ".streamlit" / "quality_ui_config.json"

DEFAULT_UI_CONFIG: Dict[str, Any] = {
    "dataset": "cases/math/cases_math_small.json",
    "mode": "auto",
    "samples": 4,
    "output_dir": "runs/quality_agent",
    "dry_run": True,
    "enable_llm_analysis": False,
    "enable_langfuse": False,
    "use_langgraph": True,
    "save_secrets": False,
    "solver": {
        "base_url": "",
        "api_key": "",
        "model_name": "",
        "temperature": 0.1,
        "top_p": 0.95,
        "max_tokens": 1024,
    },
    "agent": {
        "base_url": "",
        "api_key": "",
        "model_name": "",
        "temperature": 0.1,
        "top_p": 0.95,
        "max_tokens": 1024,
    },
    "langfuse": {
        "public_key": "",
        "secret_key": "",
        "base_url": "https://cloud.langfuse.com",
    },
}


def _merge_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    """用默认配置补齐旧配置文件中缺少的字段。"""

    merged = deepcopy(DEFAULT_UI_CONFIG)
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def load_ui_config() -> Dict[str, Any]:
    """读取本地 UI 配置；文件不存在或损坏时返回默认值。"""

    if not UI_CONFIG_FILE.exists():
        return deepcopy(DEFAULT_UI_CONFIG)

    try:
        data = json.loads(UI_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(DEFAULT_UI_CONFIG)

    if not isinstance(data, dict):
        return deepcopy(DEFAULT_UI_CONFIG)
    return _merge_defaults(data)


def save_ui_config(config: Dict[str, Any]) -> Path:
    """覆盖写入本地 UI 配置。"""

    UI_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    UI_CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return UI_CONFIG_FILE


def clear_ui_config() -> None:
    """删除已保存的 UI 配置。"""

    if UI_CONFIG_FILE.exists():
        UI_CONFIG_FILE.unlink()
