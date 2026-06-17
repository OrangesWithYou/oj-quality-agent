"""常见原始字段名到标准字段的规则映射。"""

from __future__ import annotations

import re
from typing import Dict, Iterable


FIELD_ALIASES: Dict[str, set[str]] = {
    "id": {"id", "ID", "qid", "question_id", "questionId", "problem_id", "case_id", "题目ID", "试题ID", "编号"},
    "type": {"type", "gtupe", "question_type", "questionType", "题型", "题目类型", "category"},
    "question": {"question", "problem_text", "stem", "title", "content", "body", "题干", "题目", "题目内容"},
    "options": {"options", "choices", "choiceItems", "option_list", "optionList", "选项", "答案选项"},
    "answer": {
        "answer",
        "correct",
        "right",
        "right_answer",
        "correct_answer",
        "expected_output",
        "expectedOutput",
        "key",
        "答案",
        "正确答案",
    },
    "analysis": {"analysis", "explanation", "explain", "solution", "解析", "答案解析", "详解"},
    "knowledge_points": {"knowledge_points", "knowledgePoints", "keypoint", "knowledge", "tags", "考点", "知识点"},
    "language": {"language", "lang", "语言"},
    "difficulty": {"difficulty", "hard_level", "hardLevel", "metadata.difficulty", "level", "难度", "难易程度"},
    "subject": {"subject", "course", "学科", "科目"},
    "grade": {"grade", "stage", "学段", "年级", "适用年级"},
    "is_contest": {"is_contest", "isContest", "contest", "是否竞赛题", "竞赛题"},
    "contest_type": {"contest_type", "contestType", "contest_name", "赛事类型", "竞赛类型", "赛事名称"},
    "source": {"source", "metadata.source", "origin", "出处", "来源", "题目来源"},
}


def normalize_key(value: str) -> str:
    """把字段名规整为便于比较的形式。"""

    return re.sub(r"[\s_\-:：,，;；/\\]+", "", str(value or "").strip().lower())


def alias_lookup(source_fields: Iterable[str]) -> Dict[str, str]:
    """基于字段名别名返回标准字段 -> 原始字段的初步映射。"""

    normalized_sources = {normalize_key(field): field for field in source_fields}
    mapping: Dict[str, str] = {}
    for target_field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            source = normalized_sources.get(normalize_key(alias))
            if source is not None:
                mapping[target_field] = source
                break
    return mapping
