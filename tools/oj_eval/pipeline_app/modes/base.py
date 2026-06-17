from __future__ import annotations

"""BaseMode：所有运行模式的抽象基类。"""

import argparse
from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseMode(ABC):
    """运行模式接口。

    新增模式只需：
    1. 继承 BaseMode 并实现 run()
    2. 在 mode_registry.py 中注册
    """

    #: 模式名称，用于 --domain 或 --run-xxx 参数匹配
    name: str = ""

    def __init__(self, args: argparse.Namespace):
        self.args = args

    @abstractmethod
    def run(self) -> Dict[str, Any]:
        """执行本模式的主流程，返回结构化结果摘要。"""
        ...
