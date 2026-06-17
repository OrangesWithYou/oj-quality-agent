"""报告输出层。

负责把工作流状态落盘成机器可读 JSON、人工可读 Markdown、
以及可单独处理的人工复核队列。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List


DATASET_TYPE_LABELS = {
    "math": "客观题",
    "code": "编程题",
    "mixed": "混合题库",
    "auto": "自动识别",
}

RISK_FLAG_LABELS = {
    "dry_run_no_solver_evidence": "演练模式：未调用评测模型",
    "no_solver_evidence": "缺少模型评测证据",
    "low_pass_rate": "通过率偏低",
    "suspect_bad_item": "疑似坏题",
    "completeness_error": "字段缺失或结构错误",
    "completeness_warning": "字段不完整或格式可疑",
    "invalid_samples": "存在无效采样",
    "excluded_from_accuracy": "未计入正确率",
}


def _write_json(path: Path, data: Any) -> None:
    """写 JSON 文件并自动创建父目录。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _pct(value: float) -> str:
    """把 0-1 浮点数格式化为百分比文本。"""

    return f"{value * 100:.2f}%"


def _enabled(value: bool) -> str:
    """把布尔值转成更适合报告阅读的中文。"""

    return "是" if value else "否"


def _dataset_type_label(value: str) -> str:
    """把内部题库类型转成中文展示。"""

    return DATASET_TYPE_LABELS.get(value, value or "未知")


def _risk_text(flags: List[str]) -> str:
    """把内部风险标签转成中文说明。"""

    if not flags:
        return "暂无明显风险"
    return "；".join(RISK_FLAG_LABELS.get(flag, flag) for flag in flags)


def _pass_rate_text(item: Dict[str, Any]) -> str:
    """没有真实评测证据时，避免把 0% 写成真实正确率。"""

    flags = item.get("risk_flags", [])
    if "dry_run_no_solver_evidence" in flags or "no_solver_evidence" in flags:
        return "无评测数据"
    return _pct(float(item.get("sample_pass_rate", 0.0) or 0.0))


def _cell(value: Any) -> str:
    """转义 Markdown 表格单元格里会破坏格式的竖线。"""

    return str(value or "").replace("|", "\\|")


def build_review_queue(results: List[Dict[str, Any]], llm_findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """生成需要人工复核的题目清单。"""

    llm_by_id = {str(item.get("problem_id")): item for item in llm_findings}
    queue: List[Dict[str, Any]] = []
    for result in results:
        if not result.get("need_human_review"):
            continue
        item = dict(result)
        if str(item.get("problem_id")) in llm_by_id:
            # 将 Agent 模型归因合并到复核项，方便复核人员直接看原因。
            item["llm_analysis"] = llm_by_id[str(item.get("problem_id"))]
        queue.append(item)
    return queue


def _markdown_report(state: Dict[str, Any], review_queue: List[Dict[str, Any]]) -> str:
    """生成 Markdown 报告正文。"""

    config = state["config"]
    results = state.get("quality_results", [])
    difficulty_counter = Counter(item.get("agent_difficulty", "unknown") for item in results)
    risk_counter = Counter(flag for item in results for flag in item.get("risk_flags", []))
    dataset_summary = state.get("dataset_summary", {})
    is_dry_run_only = bool(results) and all(
        "dry_run_no_solver_evidence" in item.get("risk_flags", []) for item in results
    )

    lines = [
        "# 难题质检报告",
        "",
        "## 本次运行信息",
        "",
        f"- 数据集文件：`{config.dataset_path}`",
        f"- 用户要求：{config.user_instruction or '无'}",
        f"- 识别出的题库类型：{_dataset_type_label(str(state.get('dataset_type', '')))}",
        f"- 每题采样次数：{config.samples}",
        f"- 是否为演练模式（Dry run）：{_enabled(config.dry_run)}",
        f"- 是否启用 Agent 模型分析：{_enabled(config.enable_llm_analysis)}",
        f"- 是否启用 Langfuse 轨迹记录：{_enabled(config.enable_langfuse)}",
        "",
        "## 结果总览",
        "",
        f"- 题目总数：{dataset_summary.get('total_items', len(results))}",
        f"- 已生成质检结果：{len(results)}",
        f"- 建议人工复核：{len(review_queue)}",
        f"- 完整性检查发现问题：{len(state.get('completeness_findings', []))}",
        "",
    ]
    if is_dry_run_only:
        lines.extend(
            [
                "## 重要说明",
                "",
                "- 当前开启了 Dry run，只完成结构检查，没有调用评测模型。",
                "- 因此本报告不会给出真实正确率，难度会显示为“未评测”。",
                "- 如需真实难度判断，请在界面左侧关闭 Dry run，并配置可用的评测模型。",
                "",
            ]
        )

    lines.extend(
        [
            "## 难度分布",
            "",
        ]
    )
    if difficulty_counter:
        # Counter 保留出现顺序，报告里按本次结果出现顺序展示。
        for difficulty, count in difficulty_counter.items():
            lines.append(f"- {difficulty}：{count} 道")
    else:
        lines.append("- 暂无难度结果。")

    lines.extend(["", "## 风险说明", ""])
    if risk_counter:
        for risk, count in risk_counter.items():
            lines.append(f"- {_risk_text([risk])}：{count} 道")
    else:
        lines.append("- 暂无明显风险。")

    lines.extend(
        [
            "",
            "## 题目明细",
            "",
            "| 题目 ID | 题型 | 通过率 | 难度判断 | 是否建议人工复核 | 风险说明 |",
            "|---|---|---:|---|---|---|",
        ]
    )
    for item in results:
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} |".format(
                _cell(item.get("problem_id", "")),
                _cell(item.get("question_type", "")),
                _cell(_pass_rate_text(item)),
                _cell(item.get("agent_difficulty", "")),
                "是" if item.get("need_human_review") else "否",
                _cell(_risk_text(item.get("risk_flags", []))),
            )
        )

    if state.get("warnings"):
        lines.extend(["", "## 警告", ""])
        for warning in state.get("warnings", []):
            lines.append(f"- {warning}")

    if state.get("errors"):
        lines.extend(["", "## 错误", ""])
        for error in state.get("errors", []):
            lines.append(f"- {error}")

    return "\n".join(lines) + "\n"


def write_report_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """LangGraph 节点：输出所有报告文件并回写路径。"""

    run_dir = state["run_dir"]
    review_queue = build_review_queue(state.get("quality_results", []), state.get("llm_findings", []))

    # summary 是主 JSON，保留所有关键证据，供 UI、脚本或后续平台读取。
    summary = {
        "config": state["config"].redacted(),
        "dataset_summary": state.get("dataset_summary", {}),
        "eval_summary": state.get("eval_summary", {}),
        "quality_results": state.get("quality_results", []),
        "llm_findings": state.get("llm_findings", []),
        "review_queue_count": len(review_queue),
        "warnings": state.get("warnings", []),
        "errors": state.get("errors", []),
    }
    paths = {
        "quality_summary": str(run_dir / "quality_summary.json"),
        "quality_report": str(run_dir / "quality_report.md"),
        "review_queue": str(run_dir / "review_queue.json"),
        "completeness_findings": str(run_dir / "completeness_findings.json"),
        "trace_metadata": str(run_dir / "trace_metadata.json"),
    }
    # 五类产物分别服务不同消费场景：总览、人工报告、复核队列、结构问题、追踪元数据。
    _write_json(Path(paths["quality_summary"]), summary)
    _write_json(Path(paths["review_queue"]), review_queue)
    _write_json(Path(paths["completeness_findings"]), state.get("completeness_findings", []))
    _write_json(Path(paths["trace_metadata"]), state.get("trace_metadata", {}))
    Path(paths["quality_report"]).write_text(_markdown_report(state, review_queue), encoding="utf-8")

    return {**state, "review_queue": review_queue, "report_paths": paths}
