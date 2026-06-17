"""数据集识别与完整性检查。

该模块在昂贵的模型调用之前运行，用低成本规则先发现字段缺失、
题型不明、重复 ID 等结构性问题。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..standardization.normalizer import load_json_items


MATH_TYPE_KEYS = ("gtupe", "question_type", "questionType", "type", "题型")


def load_dataset(path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """读取 JSON/JSONL 数据集，并统一提取题目列表。

    兼容四种常见外层格式：
    - [...]
    - {"cases": [...]}
    - {"questions": [...]}
    - 每行一个题目对象的 JSONL
    """

    if not path.exists():
        raise FileNotFoundError(f"Dataset file does not exist: {path}")

    return load_json_items(path)


def _text(value: Any) -> str:
    """把任意值规整成去首尾空白的字符串。"""

    return "" if value is None else str(value).strip()


def detect_item_type(item: Dict[str, Any]) -> str:
    """粗略识别单题类型：编程题 / 客观题 / 未知。"""

    standard_type = _text(item.get("type")).lower()
    if standard_type in {"programming", "code", "oj"} or _text(item.get("problem_text")):
        return "code"
    if (
        standard_type in {"single_choice", "multiple_choice", "judge_choice"}
        or _text(item.get("question"))
        or _text(item.get("answer"))
        or any(_text(item.get(k)) for k in MATH_TYPE_KEYS)
    ):
        return "math"
    return "unknown"


def item_id(item: Dict[str, Any], index: int) -> str:
    """提取题目 ID；缺省时用题目序号兜底。"""

    return _text(item.get("problem_id") or item.get("ID") or item.get("id") or index)


def _has_choices(item: Dict[str, Any]) -> bool:
    """兼容旧 choices 字段和标准 options 字段。"""

    choices = item.get("choices")
    if _text(choices):
        return True
    options = item.get("options")
    return isinstance(options, list) and len(options) > 0


def _is_choice_question(question_type: str) -> bool:
    """识别中文题型和标准 JSON 题型中的选择题。"""

    normalized = _text(question_type).lower()
    return "选" in normalized or normalized in {"single_choice", "multiple_choice"}


def detect_dataset_type(items: List[Dict[str, Any]], requested_mode: str = "auto") -> str:
    """根据题目字段自动识别整批数据集类型。"""

    if requested_mode != "auto":
        return requested_mode

    counts = Counter(detect_item_type(item) for item in items)
    known = {key for key in ("code", "math") if counts.get(key, 0) > 0}
    if len(known) > 1:
        return "mixed"
    if "code" in known:
        return "code"
    if "math" in known:
        return "math"
    return "unknown"


def _add_finding(
    findings: List[Dict[str, Any]],
    *,
    item_id_value: str,
    severity: str,
    code: str,
    message: str,
) -> None:
    """追加一条完整性问题记录。"""

    findings.append(
        {
            "item_id": item_id_value,
            "severity": severity,
            "code": code,
            "message": message,
        }
    )


def check_completeness(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """执行轻量完整性检查。

    当前只做确定性字段检查，不调用模型，适合在 dry-run 中快速验收数据结构。
    """

    findings: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()

    for index, item in enumerate(items, start=1):
        current_id = item_id(item, index)
        if current_id in seen_ids:
            duplicate_ids.add(current_id)
        seen_ids.add(current_id)

        # 编程题至少需要题面；stdin/expected_output 缺失时先记 warning，
        # 因为某些数据集可能把测试用例放在其他字段或外部文件中。
        item_type = detect_item_type(item)
        if item_type == "code":
            if not _text(item.get("problem_text") or item.get("question")):
                _add_finding(findings, item_id_value=current_id, severity="error", code="missing_problem_text", message="Code item is missing problem_text/question.")
            if not _text(item.get("stdin")):
                _add_finding(findings, item_id_value=current_id, severity="warning", code="missing_stdin", message="Code item has no stdin sample.")
            if not _text(item.get("expected_output") or item.get("answer")):
                _add_finding(findings, item_id_value=current_id, severity="warning", code="missing_expected_output", message="Code item has no expected_output/answer.")
        elif item_type == "math":
            # 客观题的题干和答案是判分最小闭环，缺失时记 error。
            if not _text(item.get("question")):
                _add_finding(findings, item_id_value=current_id, severity="error", code="missing_question", message="Objective item is missing question.")
            if not _text(item.get("answer")):
                _add_finding(findings, item_id_value=current_id, severity="error", code="missing_answer", message="Objective item is missing answer.")
            question_type = next((_text(item.get(k)) for k in MATH_TYPE_KEYS if _text(item.get(k))), "")
            if not question_type:
                _add_finding(findings, item_id_value=current_id, severity="warning", code="missing_question_type", message="Objective item is missing question type.")
            if _is_choice_question(question_type) and not _has_choices(item):
                _add_finding(findings, item_id_value=current_id, severity="warning", code="missing_choices", message="Choice item has no choices.")
            if not _text(item.get("hard_level") or item.get("difficulty")):
                _add_finding(findings, item_id_value=current_id, severity="info", code="missing_hard_level", message="Objective item has no hard_level label.")
        else:
            _add_finding(findings, item_id_value=current_id, severity="error", code="unknown_item_type", message="Item type could not be detected.")

    for duplicate_id in sorted(duplicate_ids):
        # 重复 ID 会影响后续 findings 与评测结果的对齐，需要单独标记。
        _add_finding(
            findings,
            item_id_value=duplicate_id,
            severity="warning",
            code="duplicate_id",
            message=f"Duplicate item id detected: {duplicate_id}",
        )
    return findings


def inspect_dataset_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """LangGraph 节点：读取数据集并写入识别摘要。"""

    config = state["config"]
    items, raw_shape = load_dataset(config.dataset_path)
    dataset_type = detect_dataset_type(items, config.mode)
    counts = Counter(detect_item_type(item) for item in items)
    findings = check_completeness(items)
    return {
        **state,
        "dataset_type": dataset_type,
        "dataset_items": items,
        "completeness_findings": findings,
        "dataset_summary": {
            "path": str(config.dataset_path),
            "raw_shape": raw_shape,
            "total_items": len(items),
            "detected_type": dataset_type,
            "item_type_counts": dict(counts),
            "finding_count": len(findings),
        },
    }
