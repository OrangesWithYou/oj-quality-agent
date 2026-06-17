"""核心数据结构层。

放置跨 UI、CLI、工作流共享的配置和状态定义。
"""

from .config import LangfuseConfig, ModelConfig, QualityAgentConfig
from .state import QualityState

__all__ = ["LangfuseConfig", "ModelConfig", "QualityAgentConfig", "QualityState"]
