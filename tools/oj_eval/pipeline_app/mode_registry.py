from __future__ import annotations

"""模式注册表：集中管理所有可用运行模式。

新增模式只需：
1. 在 modes/ 下新建 xxx_mode.py 继承 BaseMode
2. 在此文件 REGISTRY 中添加一行
"""

import argparse
from typing import Dict, Type

from .modes.base import BaseMode
from .modes.code_mode import CodeMode
from .modes.math_mode import MathMode

# 注册表：mode_name -> Mode 类
REGISTRY: Dict[str, Type[BaseMode]] = {
    CodeMode.name: CodeMode,
    MathMode.name: MathMode,
}


def get_mode(args: argparse.Namespace) -> BaseMode:
    """根据 args 自动选择并实例化对应模式。

    判断逻辑：
    - --run-math-quiz / --math  -> MathMode
    - 其他                      -> CodeMode（默认）
    """
    if getattr(args, "run_math_quiz", False):
        return MathMode(args)
    return CodeMode(args)
