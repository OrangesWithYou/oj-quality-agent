"""配置模型。

这里集中定义 UI、CLI 和工作流共享的配置结构，避免参数散落在各个节点里。
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass
class ModelConfig:
    """OpenAI 兼容模型配置。

    项目把模型分成两个角色：
    - solver_model：调用现有 oj-eval 工具做题；
    - agent_model：用于归因解释和复核建议。
    """

    base_url: str = ""
    api_key: str = ""
    model_name: str = ""
    temperature: float = 0.1
    top_p: float = 0.95
    max_tokens: int = 1024

    @classmethod
    def from_env(cls, prefix: str, *, fallback_prefix: str = "PPIO") -> "ModelConfig":
        """从环境变量读取模型配置。

        例如 prefix=SOLVER 时优先读取 SOLVER_BASE_URL，再回退到 PPIO_BASE_URL。
        """

        return cls(
            base_url=os.getenv(f"{prefix}_BASE_URL") or os.getenv(f"{fallback_prefix}_BASE_URL", ""),
            api_key=os.getenv(f"{prefix}_API_KEY") or os.getenv(f"{fallback_prefix}_API_KEY", ""),
            model_name=os.getenv(f"{prefix}_MODEL") or os.getenv(f"{fallback_prefix}_MODEL", ""),
            temperature=float(os.getenv(f"{prefix}_TEMPERATURE", "0.1")),
            top_p=float(os.getenv(f"{prefix}_TOP_P", "0.95")),
            max_tokens=int(os.getenv(f"{prefix}_MAX_TOKENS", "1024")),
        )

    def redacted(self) -> Dict[str, Any]:
        """返回可写入报告的脱敏配置，避免 API Key 泄露。"""

        data = asdict(self)
        data["api_key"] = "***" if self.api_key else ""
        return data


@dataclass
class LangfuseConfig:
    """Langfuse 轨迹记录配置。

    UI / CLI 可以显式传入；未传时从环境变量读取。
    """

    public_key: str = ""
    secret_key: str = ""
    base_url: str = ""

    @classmethod
    def from_env(cls) -> "LangfuseConfig":
        """从环境变量读取 Langfuse 配置。"""

        return cls(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
            base_url=os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST", ""),
        )

    def normalized(self) -> "LangfuseConfig":
        """用环境变量补齐未填写的字段。"""

        env_config = self.from_env()
        self.public_key = self.public_key or env_config.public_key
        self.secret_key = self.secret_key or env_config.secret_key
        self.base_url = self.base_url or env_config.base_url
        return self

    def is_complete(self) -> bool:
        """判断是否具备创建 Langfuse callback 的最低配置。"""

        return bool(self.public_key and self.secret_key)

    def apply_to_env(self) -> None:
        """写入当前进程环境，供 Langfuse SDK 读取。"""

        if self.public_key:
            os.environ["LANGFUSE_PUBLIC_KEY"] = self.public_key
        if self.secret_key:
            os.environ["LANGFUSE_SECRET_KEY"] = self.secret_key
        if self.base_url:
            os.environ["LANGFUSE_BASE_URL"] = self.base_url
            os.environ["LANGFUSE_HOST"] = self.base_url

    def redacted(self) -> Dict[str, Any]:
        """返回可写入报告的脱敏配置。"""

        return {
            "public_key": "***" if self.public_key else "",
            "secret_key": "***" if self.secret_key else "",
            "base_url": self.base_url,
        }


@dataclass
class QualityAgentConfig:
    """单次质检任务的运行配置。"""

    dataset_path: Path
    user_instruction: str = ""
    mode: str = "auto"
    samples: int = 4
    output_dir: Path = Path("runs/quality_agent")
    dry_run: bool = False
    enable_llm_analysis: bool = False
    enable_langfuse: bool = False
    solver_model: ModelConfig | None = None
    agent_model: ModelConfig | None = None
    langfuse: LangfuseConfig | None = None

    def normalized(self) -> "QualityAgentConfig":
        """规整路径、采样次数和默认模型配置。

        Graph 入口会先调用该方法，保证后续节点拿到的是稳定类型。
        """

        self.dataset_path = Path(self.dataset_path)
        self.output_dir = Path(self.output_dir)
        self.samples = max(1, int(self.samples))
        self.solver_model = self.solver_model or ModelConfig.from_env("SOLVER")
        self.agent_model = self.agent_model or ModelConfig.from_env("AGENT")
        self.langfuse = (self.langfuse or LangfuseConfig.from_env()).normalized()
        return self

    def redacted(self) -> Dict[str, Any]:
        """返回写入报告的脱敏任务配置。"""

        return {
            "dataset_path": str(self.dataset_path),
            "user_instruction": self.user_instruction,
            "mode": self.mode,
            "samples": self.samples,
            "output_dir": str(self.output_dir),
            "dry_run": self.dry_run,
            "enable_llm_analysis": self.enable_llm_analysis,
            "enable_langfuse": self.enable_langfuse,
            "solver_model": (self.solver_model or ModelConfig()).redacted(),
            "agent_model": (self.agent_model or ModelConfig()).redacted(),
            "langfuse": (self.langfuse or LangfuseConfig()).redacted(),
        }
