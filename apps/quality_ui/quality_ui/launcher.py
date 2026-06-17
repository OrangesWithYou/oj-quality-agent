"""安装后的 UI 启动命令。

`oj-quality-ui` 会调用该模块，用当前 Python 环境启动 Streamlit。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    """从 console script 启动 Streamlit UI。"""

    app_path = Path(__file__).with_name("app.py")
    # 使用 `python -m streamlit` 可以避免 PATH 中 streamlit 命令不可见的问题。
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app_path)], check=True)


if __name__ == "__main__":
    main()
