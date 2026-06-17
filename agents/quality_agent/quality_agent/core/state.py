"""LangGraph 状态定义。

QualityState 是所有工作流节点之间传递的共享状态。每个节点只读取自己
需要的字段，并把新增结果合并回 state。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, TypedDict

from .config import QualityAgentConfig


class QualityState(TypedDict, total=False):
    """质检 Agent 工作流状态。

    total=False 表示字段可以逐步补齐，符合 LangGraph 节点逐步产出结果的方式。
    """

    # 输入配置与本次运行目录。
    config: QualityAgentConfig
    run_dir: Path

    # 数据集识别和完整性检查结果。
    dataset_type: str
    dataset_items: List[Dict[str, Any]]
    dataset_summary: Dict[str, Any]
    completeness_findings: List[Dict[str, Any]]

    # 调用现有评测工具后的证据和规则评分结果。
    eval_summary: Dict[str, Any]
    quality_results: List[Dict[str, Any]]

    # Agent 模型分析、人工复核和报告输出。
    llm_findings: List[Dict[str, Any]]
    review_queue: List[Dict[str, Any]]
    report_paths: Dict[str, str]
    trace_metadata: Dict[str, Any]

    # 不中断工作流的错误和警告统一放在状态里，最终写入报告。
    errors: List[str]
    warnings: List[str]
