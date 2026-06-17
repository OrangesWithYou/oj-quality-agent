from __future__ import annotations

"""兼容入口：保留历史命令 `python run_cases_file.py ...`。

实际 CLI 与应用编排已经拆分到 `pipeline_app` 模块：
- `pipeline_app.cli`
- `pipeline_app.application`
- `pipeline_app.case_service`
- `pipeline_app.constants`
"""

import sys
from pathlib import Path

# 源码结构已经把 OJ 工具层收进 tools/oj_eval。直接运行本文件时，
# 需要把工具源码根加入 sys.path；安装成命令后则由打包配置处理。
_TOOL_SOURCE_ROOT = Path(__file__).resolve().parent / "tools" / "oj_eval"
if _TOOL_SOURCE_ROOT.exists() and str(_TOOL_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOL_SOURCE_ROOT))

from pipeline_app.cli import build_arg_parser, main, run_pipeline

# 对外导出统一接口，方便旧脚本继续 import 使用。
__all__ = [
    "build_arg_parser",
    "run_pipeline",
    "main",
]


if __name__ == "__main__":
    # 命令行直接执行时，转发到新 CLI 模块。
    main()
