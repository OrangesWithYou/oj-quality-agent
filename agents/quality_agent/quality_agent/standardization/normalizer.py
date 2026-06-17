"""原始 JSON 到标准试题 JSON 的规则标准化。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .field_aliases import alias_lookup, normalize_key
from .schema import REQUIRED_FIELD_NAMES, STANDARD_FIELD_NAMES


FALLBACK_SOURCE_PATHS: Dict[str, List[str]] = {
    "id": ["id", "ID", "problem_id", "case_id", "qid", "question_id", "questionId", "编号"],
    "type": ["type", "gtupe", "question_type", "questionType", "题型", "题目类型"],
    "question": ["question", "problem_text", "stem", "title", "content", "body", "题干", "题目", "题目内容"],
    "options": ["options", "choices", "choiceItems", "option_list", "optionList", "选项", "答案选项"],
    "answer": ["answer", "correct_answer", "right_answer", "expected_output", "expectedOutput", "key", "答案", "正确答案"],
    "analysis": ["analysis", "explanation", "explain", "solution", "解析", "答案解析", "详解"],
    "knowledge_points": ["knowledge_points", "knowledgePoints", "keypoint", "knowledge", "tags", "考点", "知识点"],
    "language": ["language", "lang", "语言"],
    "difficulty": ["difficulty", "hard_level", "hardLevel", "metadata.difficulty", "level", "难度", "难易程度"],
    "subject": ["subject", "course", "学科", "科目"],
    "grade": ["grade", "stage", "学段", "年级", "适用年级"],
    "is_contest": ["is_contest", "isContest", "contest", "是否竞赛题", "竞赛题"],
    "contest_type": ["contest_type", "contestType", "contest_name", "赛事类型", "竞赛类型", "赛事名称"],
    "source": ["source", "metadata.source", "origin", "出处", "来源", "题目来源"],
}


@dataclass
class MappingSuggestion:
    """字段映射建议。"""

    mapping: Dict[str, str]
    confidence: Dict[str, float]
    source_fields: List[str]


@dataclass
class NormalizationResult:
    """标准化结果。"""

    standard_items: List[Dict[str, Any]]
    quality_items: List[Dict[str, Any]]
    mapping: Dict[str, str]
    report: Dict[str, Any]


def load_json_or_jsonl(path: Path) -> Tuple[Any, Dict[str, Any]]:
    """读取普通 JSON 或 JSONL。

    JSONL 按“每个非空行是一道题目对象”处理，这样后续字段映射、人工确认、
    标准化输出仍然复用同一条流程。
    """

    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError("文件为空，无法读取题目数据。")

    try:
        return json.loads(text), {"source_format": "json"}
    except json.JSONDecodeError as json_error:
        items: List[Dict[str, Any]] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL 第 {line_no} 行不是合法 JSON：{exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"JSONL 第 {line_no} 行必须是 JSON 对象。")
            items.append(item)

        if not items:
            raise ValueError("文件不是合法 JSON，也没有可读取的 JSONL 对象行。") from json_error
        return items, {"source_format": "jsonl", "outer_type": "jsonl", "line_count": len(items)}


def load_json_items(path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """读取 JSON/JSONL 并抽取题目列表。"""

    raw, base_shape = load_json_or_jsonl(path)
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)], {"outer_type": "list", **base_shape}
    if isinstance(raw, dict):
        for key in ("cases", "questions", "items", "data", "records"):
            value = raw.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)], {
                    "outer_type": "dict",
                    **base_shape,
                    "list_key": key,
                    "top_keys": sorted(raw.keys()),
                }
        return [raw], {"outer_type": "single_object", **base_shape, "top_keys": sorted(raw.keys())}
    raise ValueError("文件必须是 JSON 数组、JSON 对象，或每行一个 JSON 对象的 JSONL。")


def _iter_paths(value: Any, *, prefix: str = "", depth: int = 0, max_depth: int = 2) -> Iterable[str]:
    """枚举浅层字段路径，避免把复杂内容全部展开。"""

    if depth > max_depth or not isinstance(value, dict):
        return
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        yield path
        if isinstance(child, dict):
            yield from _iter_paths(child, prefix=path, depth=depth + 1, max_depth=max_depth)


def collect_source_fields(items: List[Dict[str, Any]], *, limit: int = 20) -> List[str]:
    """收集样例题目中出现过的字段路径。"""

    fields: List[str] = []
    seen: set[str] = set()
    for item in items[:limit]:
        for path in _iter_paths(item):
            if path not in seen:
                seen.add(path)
                fields.append(path)
    return fields


def _get_path(item: Dict[str, Any], path: str) -> Any:
    """按点路径读取字段。"""

    current: Any = item
    for part in str(path or "").split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _non_empty(value: Any) -> str:
    """安全转字符串并去掉空值标记。"""

    text = "" if value is None else str(value).strip()
    return "" if text.lower() in {"", "null", "none", "nan"} else text


def _has_content(value: Any) -> bool:
    """判断字段值是否有业务内容；空字符串、空数组、null 均视为空。"""

    if value in (None, "", [], {}):
        return False
    if isinstance(value, str):
        return bool(_non_empty(value))
    return True


def _candidate_paths(field: str, mapped_path: str | None) -> List[str]:
    """返回某个标准字段的候选原始路径，优先使用人工确认映射。"""

    result: List[str] = []
    for path in [mapped_path or "", *FALLBACK_SOURCE_PATHS.get(field, [])]:
        if path and path not in result:
            result.append(path)
    return result


def _first_content_value(item: Dict[str, Any], paths: List[str]) -> Tuple[str, Any]:
    """按候选路径取第一个非空值；都为空时返回第一个存在路径的原值。"""

    first_existing_path = ""
    first_existing_value: Any = None
    for path in paths:
        value = _get_path(item, path)
        if value is not None and not first_existing_path:
            first_existing_path = path
            first_existing_value = value
        if _has_content(value):
            return path, value
    return first_existing_path, first_existing_value


def _looks_like_options(value: Any) -> bool:
    if isinstance(value, list) and value:
        return True
    text = _non_empty(value)
    return bool(re.search(r"(^|\n)\s*[A-H][\.．、)]", text))


def _looks_like_answer(value: Any) -> bool:
    if isinstance(value, list):
        return True
    text = _non_empty(value)
    return bool(re.fullmatch(r"[A-H]+", text.upper()) or text in {"正确", "错误", "对", "错"})


def _looks_like_type(value: Any) -> bool:
    text = _non_empty(value).lower()
    return any(token in text for token in ["单选", "多选", "判断", "choice", "judge", "填空", "简答", "计算", "论述"])


def _value_shape_mapping(items: List[Dict[str, Any]], fields: List[str]) -> Dict[str, str]:
    """基于字段值形态补充猜测。"""

    scores: Dict[str, Dict[str, int]] = {}
    for field in fields:
        scores[field] = {"options": 0, "answer": 0, "type": 0, "question": 0}
        for item in items[:10]:
            value = _get_path(item, field)
            if _looks_like_options(value):
                scores[field]["options"] += 1
            if _looks_like_answer(value):
                scores[field]["answer"] += 1
            if _looks_like_type(value):
                scores[field]["type"] += 1
            text = _non_empty(value)
            if len(text) >= 12 and any(ch in text for ch in "？?。.\n"):
                scores[field]["question"] += 1

    result: Dict[str, str] = {}
    for target in ("options", "answer", "type", "question"):
        best_field = ""
        best_score = 0
        for field, field_scores in scores.items():
            if field_scores[target] > best_score:
                best_field = field
                best_score = field_scores[target]
        if best_score > 0:
            result[target] = best_field
    return result


def guess_field_mapping(items: List[Dict[str, Any]]) -> MappingSuggestion:
    """用别名和字段值形态生成初始映射建议。"""

    source_fields = collect_source_fields(items)
    mapping = alias_lookup(source_fields)
    shape_mapping = _value_shape_mapping(items, source_fields)
    for target, source in shape_mapping.items():
        mapping.setdefault(target, source)

    confidence: Dict[str, float] = {}
    normalized_sources = {normalize_key(field): field for field in source_fields}
    for target, source in mapping.items():
        confidence[target] = 0.92 if normalized_sources.get(normalize_key(source)) else 0.75
    return MappingSuggestion(mapping=mapping, confidence=confidence, source_fields=source_fields)


def _normalize_type(value: Any) -> str:
    text = _non_empty(value).lower()
    if not text:
        return ""
    if "program" in text or "code" in text or "oj" in text or "编程" in text or "代码" in text:
        return "programming"
    if "多选" in text or "multiple" in text:
        return "multiple_choice"
    if "判断" in text or "judge" in text or "true" in text or "false" in text:
        return "judge_choice"
    if "单选" in text or "single" in text or "choice" in text:
        return "single_choice"
    if "填空" in text or "blank" in text:
        return "fill_blank"
    if "简答" in text or "short" in text:
        return "short_answer"
    if "计算" in text or "calculation" in text:
        return "calculation_question"
    if "论述" in text or "essay" in text:
        return "essay_question"
    return text


def _infer_type(raw_item: Dict[str, Any], current_type: str) -> str:
    """在源数据没有题型字段时，根据原始结构做保守推断。"""

    if _non_empty(current_type):
        return current_type
    if _non_empty(_get_path(raw_item, "problem_text")) or _non_empty(_get_path(raw_item, "stdin")):
        return "programming"
    options = _get_path(raw_item, "options")
    if options is None:
        options = _get_path(raw_item, "choices")
    answer = _get_path(raw_item, "answer")
    if _has_content(options):
        letters = re.findall(r"[A-H]", _non_empty(answer).upper())
        return "multiple_choice" if len(set(letters)) > 1 else "single_choice"
    return ""


def _normalize_options(value: Any) -> List[Dict[str, str]]:
    if value is None:
        return []
    if isinstance(value, list):
        options: List[Dict[str, str]] = []
        for index, item in enumerate(value):
            default_label = chr(ord("A") + index)
            if isinstance(item, dict):
                label = _non_empty(item.get("label") or item.get("key") or item.get("id") or item.get("option") or default_label)
                text = _non_empty(item.get("text") or item.get("value") or item.get("content") or item.get("name"))
                options.append({"label": label or default_label, "text": text})
            else:
                text = _non_empty(item)
                match = re.match(r"^\s*([A-H])[\.\uFF0E、)]\s*(.*)$", text, flags=re.I)
                if match:
                    options.append({"label": match.group(1).upper(), "text": match.group(2).strip()})
                else:
                    options.append({"label": default_label, "text": text})
        return options

    text = _non_empty(value)
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
    if len(parts) <= 1:
        parts = [part.strip() for part in re.split(r"(?=[A-H][\.\uFF0E、)])", text) if part.strip()]
    return _normalize_options(parts)


def _normalize_answer(value: Any, question_type: str) -> Any:
    if isinstance(value, list):
        return [_non_empty(item).upper() for item in value if _non_empty(item)]
    text = _non_empty(value)
    if not text:
        return [] if question_type == "multiple_choice" else ""
    letters = re.findall(r"[A-H]", text.upper())
    if question_type == "multiple_choice":
        return sorted(set(letters)) if letters else [text]
    if question_type == "single_choice" and letters:
        return letters[0]
    if question_type == "judge_choice":
        if text.lower() in {"true", "t", "yes", "y", "1", "正确", "对", "√"}:
            return "正确"
        if text.lower() in {"false", "f", "no", "n", "0", "错误", "错", "×"}:
            return "错误"
    return text


def _normalize_knowledge_points(value: Any) -> List[Dict[str, str]]:
    if value is None:
        return []
    if isinstance(value, list):
        result: List[Dict[str, str]] = []
        for index, item in enumerate(value, start=1):
            if isinstance(item, dict):
                name = _non_empty(item.get("name") or item.get("label") or item.get("title") or item.get("id"))
                result.append(
                    {
                        "id": _non_empty(item.get("id")) or str(index),
                        "name": name,
                        "path": _non_empty(item.get("path")) or name,
                    }
                )
            else:
                name = _non_empty(item)
                if name:
                    result.append({"id": str(index), "name": name, "path": name})
        return result
    text = _non_empty(value)
    if not text:
        return []
    names = [part.strip() for part in re.split(r"[,，;；|/、\n]+", text) if part.strip()]
    return [{"id": str(index), "name": name, "path": name} for index, name in enumerate(names, start=1)]


def _normalize_difficulty(value: Any) -> str:
    text = _non_empty(value).lower()
    if not text:
        return ""
    if text in {"easy", "简单", "易", "低"}:
        return "易"
    if text in {"medium", "normal", "中等", "中", "一般"}:
        return "中"
    if any(token in text for token in ["hard", "难", "困难", "超难", "高"]):
        return "难"
    return str(value).strip()


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _non_empty(value).lower()
    return text in {"true", "1", "yes", "y", "是", "竞赛", "竞赛题"}


def _default_item(index: int) -> Dict[str, Any]:
    return {
        "id": str(index),
        "type": "",
        "question": "",
        "options": [],
        "answer": "",
        "analysis": "",
        "knowledge_points": [],
        "language": "zh",
        "difficulty": "",
        "subject": "",
        "grade": "",
        "is_contest": False,
        "contest_type": "",
        "source": "",
    }


def _legacy_question_type(value: str) -> str:
    """把标准题型转成旧数学评测器使用的中文题型。"""

    mapping = {
        "single_choice": "单选",
        "multiple_choice": "多选",
        "judge_choice": "判断",
    }
    return mapping.get(value, value)


def _options_to_choices(options: Any) -> str:
    """把标准 options 数组转成旧评测器使用的选项文本。"""

    if not isinstance(options, list):
        return _non_empty(options)
    lines: List[str] = []
    for index, option in enumerate(options):
        default_label = chr(ord("A") + index)
        if isinstance(option, dict):
            label = _non_empty(option.get("label")) or default_label
            text = _non_empty(option.get("text") or option.get("value") or option.get("content"))
            lines.append(f"{label}. {text}")
        else:
            text = _non_empty(option)
            if text:
                lines.append(text)
    return "\n".join(lines)


def _knowledge_points_text(value: Any) -> str:
    """把标准知识点数组转成旧字段的简洁文本。"""

    if not isinstance(value, list):
        return _non_empty(value)
    names: List[str] = []
    for item in value:
        if isinstance(item, dict):
            name = _non_empty(item.get("name") or item.get("label") or item.get("id"))
        else:
            name = _non_empty(item)
        if name:
            names.append(name)
    return "；".join(names)


def _quality_item_from_standard(raw_item: Dict[str, Any], standard: Dict[str, Any]) -> Dict[str, Any]:
    """生成可直接喂给现有 oj_eval 工具的兼容题目。"""

    item = dict(standard)
    question_type = str(standard.get("type") or "")
    if question_type == "programming":
        item.update(
            {
                "problem_id": standard.get("id", ""),
                "problem_text": standard.get("question", ""),
                "stdin": _non_empty(_get_path(raw_item, "stdin")),
                "expected_output": _non_empty(_get_path(raw_item, "expected_output")) or _non_empty(standard.get("answer")),
                "sample_plan": _get_path(raw_item, "sample_plan") if isinstance(_get_path(raw_item, "sample_plan"), list) else [],
                "metadata": _get_path(raw_item, "metadata") if isinstance(_get_path(raw_item, "metadata"), dict) else {},
            }
        )
    elif question_type in {"single_choice", "multiple_choice", "judge_choice"}:
        item.update(
            {
                "ID": standard.get("id", ""),
                "gtupe": _legacy_question_type(question_type),
                "choices": _options_to_choices(standard.get("options")),
                "explanation": standard.get("analysis", ""),
                "keypoint": _knowledge_points_text(standard.get("knowledge_points")),
                "hard_level": standard.get("difficulty", ""),
            }
        )
    return item


def _normalize_field(field: str, value: Any, item: Dict[str, Any]) -> Any:
    if field == "type":
        return _normalize_type(value)
    if field == "options":
        return _normalize_options(value)
    if field == "answer":
        return _normalize_answer(value, str(item.get("type") or ""))
    if field == "knowledge_points":
        return _normalize_knowledge_points(value)
    if field == "difficulty":
        return _normalize_difficulty(value)
    if field == "is_contest":
        return _normalize_bool(value)
    if field == "language":
        return _non_empty(value) or "zh"
    return _non_empty(value)


def apply_mapping(items: List[Dict[str, Any]], mapping: Dict[str, str]) -> NormalizationResult:
    """应用字段映射并生成标准 JSON。"""

    clean_mapping = {target: source for target, source in mapping.items() if target in STANDARD_FIELD_NAMES and source}
    standard_items: List[Dict[str, Any]] = []
    quality_items: List[Dict[str, Any]] = []
    missing_field_findings: List[Dict[str, Any]] = []
    empty_field_findings: List[Dict[str, Any]] = []
    source_usage: Dict[str, Dict[str, int]] = {field: {} for field in STANDARD_FIELD_NAMES}

    for index, raw_item in enumerate(items, start=1):
        standard = _default_item(index)
        for field in STANDARD_FIELD_NAMES:
            source_paths = _candidate_paths(field, clean_mapping.get(field))
            if not source_paths:
                continue
            used_path, value = _first_content_value(raw_item, source_paths)
            if used_path:
                source_usage[field][used_path] = source_usage[field].get(used_path, 0) + 1
            standard[field] = _normalize_field(field, value, standard)

        if not standard["id"]:
            standard["id"] = str(index)
        standard["type"] = _infer_type(raw_item, str(standard.get("type") or ""))
        for required in REQUIRED_FIELD_NAMES:
            if required not in standard:
                missing_field_findings.append(
                    {
                        "item_index": index,
                        "item_id": standard.get("id", str(index)),
                        "field": required,
                        "severity": "error",
                        "message": f"标准字段 {required} 缺失。",
                    }
                )
            elif not _has_content(standard.get(required)):
                empty_field_findings.append(
                    {
                        "item_index": index,
                        "item_id": standard.get("id", str(index)),
                        "field": required,
                        "severity": "info",
                        "message": f"标准字段 {required} 已存在但值为空，请按业务需要判断是否允许。",
                    }
                )
        standard_items.append(standard)
        quality_items.append(_quality_item_from_standard(raw_item, standard))

    report = {
        "generated_at": datetime.now().isoformat(),
        "total_items": len(standard_items),
        "standard_fields": STANDARD_FIELD_NAMES,
        "mapped_fields": sorted(clean_mapping.keys()),
        "missing_required_count": len(missing_field_findings),
        "empty_required_count": len(empty_field_findings),
        "source_usage": source_usage,
        "findings": [*missing_field_findings, *empty_field_findings],
    }
    return NormalizationResult(standard_items=standard_items, quality_items=quality_items, mapping=clean_mapping, report=report)


def _write_jsonl(path: Path, items: List[Dict[str, Any]]) -> None:
    """按每行一个 JSON 对象写入 JSONL。"""

    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n",
        encoding="utf-8",
    )


def save_normalization_outputs(result: NormalizationResult, output_dir: Path, output_format: str = "json") -> Dict[str, str]:
    """保存标准化产物。

    output_format 只影响题目数据文件：
    - json: standard_questions.json / quality_dataset.json，保留外层 questions/cases
    - jsonl: standard_questions.jsonl / quality_dataset.jsonl，每行一道题
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_format = output_format.strip().lower()
    if normalized_format not in {"json", "jsonl"}:
        raise ValueError("output_format 必须是 json 或 jsonl。")

    question_suffix = ".jsonl" if normalized_format == "jsonl" else ".json"
    paths = {
        "standard_questions": str(output_dir / f"standard_questions{question_suffix}"),
        "quality_dataset": str(output_dir / f"quality_dataset{question_suffix}"),
        "field_mapping": str(output_dir / "field_mapping.json"),
        "normalization_report": str(output_dir / "normalization_report.json"),
    }
    if normalized_format == "jsonl":
        _write_jsonl(Path(paths["standard_questions"]), result.standard_items)
        _write_jsonl(Path(paths["quality_dataset"]), result.quality_items)
    else:
        Path(paths["standard_questions"]).write_text(
            json.dumps({"questions": result.standard_items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        Path(paths["quality_dataset"]).write_text(
            json.dumps({"cases": result.quality_items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    Path(paths["field_mapping"]).write_text(json.dumps(result.mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(paths["normalization_report"]).write_text(json.dumps(result.report, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths
