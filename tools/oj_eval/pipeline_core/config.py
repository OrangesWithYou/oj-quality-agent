from __future__ import annotations

"""API 与运行默认配置。

所有需要修改的接口地址、密钥、模型名、默认参数都集中在这里。
不需要改动其他任何文件，只改这一个文件即可切换环境。
"""

import os

# ── PPIO 模型接口 ──────────────────────────────────────────────────────────────
# API 密钥：优先读取环境变量。不要在源码中写入真实密钥。
PPIO_API_KEY: str = os.getenv(
    "PPIO_API_KEY",
    "",
)
PPIO_BASE_URL: str = os.getenv(
    "PPIO_BASE_URL",
    "http://148.113.217.166:3000/v1",
)
PPIO_MODEL: str = os.getenv(
    "PPIO_MODEL",
    "gemini-3.1-flash-lite-preview",
)





# ── Judge0 判题接口 ────────────────────────────────────────────────────────────
# Judge0 服务地址（可替换为自建实例）。
JUDGE0_BASE_URL: str = os.getenv("JUDGE0_BASE_URL", "https://ce.judge0.com")

# 语言 ID：71 = Python 3，其他语言 ID 参见 Judge0 文档。
JUDGE0_LANGUAGE_ID: int = int(os.getenv("JUDGE0_LANGUAGE_ID", "71"))

# ── 运行默认参数 ───────────────────────────────────────────────────────────────
# 每道题默认采样次数。
DEFAULT_SAMPLES_PER_PROBLEM: int = 4

# Judge0 轮询间隔（秒）。
DEFAULT_POLL_INTERVAL_SEC: float = 1.0

# Judge0 轮询超时（秒）。
DEFAULT_POLL_TIMEOUT_SEC: int = 300

# 默认输出根目录。
DEFAULT_OUTPUT_DIR: str = "runs"
