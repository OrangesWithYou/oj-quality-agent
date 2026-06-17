"""Agent 工具适配层。

该包把外部能力封装成 Agent 可调用工具，避免工作流节点直接关心底层
CLI、HTTP 或 subprocess 细节。
"""

from .oj_eval_tool import build_oj_eval_structured_tool, invoke_oj_eval_tool, run_oj_eval_cli

__all__ = ["build_oj_eval_structured_tool", "invoke_oj_eval_tool", "run_oj_eval_cli"]

