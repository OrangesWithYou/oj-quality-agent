"""难题质检智能体包。

对外暴露最常用的配置结构和运行入口，便于其他脚本直接 import 调用。
"""

from __future__ import annotations

from .core.config import LangfuseConfig, ModelConfig, QualityAgentConfig
from .workflow.graph import run_quality_agent

# 控制 `from quality_agent import *` 的公开 API。
__all__ = ["LangfuseConfig", "ModelConfig", "QualityAgentConfig", "run_quality_agent"]
