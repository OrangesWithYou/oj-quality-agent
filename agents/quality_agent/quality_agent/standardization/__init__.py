"""题库 JSON 标准化能力。

该包负责把不同来源的 JSON 字段映射为统一试题数据模型，作为质检前置步骤。
"""

from .schema import STANDARD_FIELDS, standard_schema_rows
from .normalizer import (
    MappingSuggestion,
    NormalizationResult,
    apply_mapping,
    guess_field_mapping,
    load_json_or_jsonl,
    load_json_items,
    save_normalization_outputs,
)
from .llm_mapping import guess_mapping_with_llm
from .template_store import load_mapping_templates, save_mapping_template

__all__ = [
    "STANDARD_FIELDS",
    "MappingSuggestion",
    "NormalizationResult",
    "apply_mapping",
    "guess_field_mapping",
    "guess_mapping_with_llm",
    "load_json_or_jsonl",
    "load_json_items",
    "load_mapping_templates",
    "save_mapping_template",
    "save_normalization_outputs",
    "standard_schema_rows",
]
