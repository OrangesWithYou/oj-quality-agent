"""标准试题 JSON 字段定义。

字段来源：项目根目录上一层的《试题数据模型.xlsx》。运行时直接使用这里的
固定配置，避免 UI 每次启动都依赖 Excel 文件。
"""

from __future__ import annotations

from typing import Any, Dict, List


STANDARD_FIELDS: List[Dict[str, Any]] = [
    {"name": "id", "label": "试题唯一标识", "required": True, "description": "全局唯一"},
    {
        "name": "type",
        "label": "题目类型",
        "required": True,
        "description": "单选题 single_choice；多选题 multiple_choice；判断题 judge_choice；编程题 programming；简答题 short_answer；计算题 calculation_question；填空题 fill_blank；论述题 essay_question",
    },
    {"name": "question", "label": "题干内容", "required": True, "description": "题目内容"},
    {"name": "options", "label": "选项", "required": True, "description": "每个选项包含 label 和 text，顺序固定"},
    {"name": "answer", "label": "正确答案", "required": True, "description": "单选为 label 字符串；多选为 label 数组；空数组视为无答案"},
    {"name": "analysis", "label": "答案解析", "required": True, "description": "解释正确答案依据，可包含错误选项辨析"},
    {"name": "knowledge_points", "label": "知识点", "required": True, "description": "至少一个，含 id、name、path"},
    {"name": "language", "label": "语言", "required": True, "description": "如 zh、en"},
    {"name": "difficulty", "label": "难易程度", "required": True, "description": "值域：易、中、难"},
    {"name": "subject", "label": "学科", "required": False, "description": "如 数学、物理"},
    {"name": "grade", "label": "适用年级/学段", "required": False, "description": "如 小学四年级"},
    {"name": "is_contest", "label": "是否竞赛题", "required": False, "description": "是、否"},
    {"name": "contest_type", "label": "赛事类型", "required": False, "description": "当 is_contest 为 true 时填写"},
    {"name": "source", "label": "题目来源", "required": False, "description": "如 2024年高考全国Ⅰ卷、自建题库"},
]

STANDARD_FIELD_NAMES = [field["name"] for field in STANDARD_FIELDS]
REQUIRED_FIELD_NAMES = [field["name"] for field in STANDARD_FIELDS if field["required"]]


def standard_schema_rows() -> List[Dict[str, Any]]:
    """返回 UI 可直接展示的标准字段表。"""

    return [
        {
            "字段名": field["name"],
            "中文标签": field["label"],
            "必填": "是" if field["required"] else "推荐",
            "说明": field["description"],
        }
        for field in STANDARD_FIELDS
    ]
