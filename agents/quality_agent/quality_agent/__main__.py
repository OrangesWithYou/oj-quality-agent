"""支持 `python -m quality_agent` 的模块入口。"""

from __future__ import annotations

from .cli import main


if __name__ == "__main__":
    # 直接复用 CLI main，避免维护两套命令行逻辑。
    main()
