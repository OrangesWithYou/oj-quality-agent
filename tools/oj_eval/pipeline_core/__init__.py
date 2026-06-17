from __future__ import annotations

"""pipeline_core 包：对外统一导出，保持与旧 pipeline_core.py 完全兼容。"""

# API 与运行配置（独立文件，修改接口/密钥/模型只需改 config.py）
from .config import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_POLL_INTERVAL_SEC,
    DEFAULT_POLL_TIMEOUT_SEC,
    DEFAULT_SAMPLES_PER_PROBLEM,
    JUDGE0_BASE_URL,
    JUDGE0_LANGUAGE_ID,
    PPIO_API_KEY,
    PPIO_BASE_URL,
    PPIO_MODEL,
)

# 数据模型
from .core import (
    ProblemCase,
    RuntimeConfig,
    SamplingConfig,
    SESSION,
)

# 工具函数
from .core import (
    build_judge_status_summary,
    build_requests_session,
    collect_failed_tests,
    detect_excel_columns,
    expand_sample_plan,
    extract_code,
    find_first_diff,
    load_problems_from_excel,
    non_empty_text,
    normalize_header,
    pick_final_sample,
    read_sampling_from_row,
    safe_float,
    safe_int,
    write_json,
    write_text,
)

# 外部服务客户端
from .services import JudgeClient

# 端到端运行器
from .runner import PPIOJudgeRunner

# 日志工具
import logging
from pathlib import Path


def setup_logger(log_file: Path, logger_name: str = "ppio_judge_pipeline") -> logging.Logger:
    """配置文件 + 控制台双通道日志。"""
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


__all__ = [
    # 配置常量
    "PPIO_API_KEY", "PPIO_BASE_URL", "PPIO_MODEL",
    "JUDGE0_BASE_URL", "JUDGE0_LANGUAGE_ID",
    "DEFAULT_OUTPUT_DIR", "DEFAULT_SAMPLES_PER_PROBLEM",
    "DEFAULT_POLL_INTERVAL_SEC", "DEFAULT_POLL_TIMEOUT_SEC",
    # 数据模型
    "SamplingConfig", "ProblemCase", "RuntimeConfig", "SESSION",
    # 工具函数
    "build_requests_session", "setup_logger",
    "write_json", "write_text",
    "normalize_header", "safe_float", "safe_int", "non_empty_text",
    "extract_code", "find_first_diff",
    "collect_failed_tests", "expand_sample_plan",
    "pick_final_sample", "build_judge_status_summary",
    "detect_excel_columns", "read_sampling_from_row", "load_problems_from_excel",
    # 客户端 & 运行器
    "JudgeClient", "PPIOJudgeRunner",
]
