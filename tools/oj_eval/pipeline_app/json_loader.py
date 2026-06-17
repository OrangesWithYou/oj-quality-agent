from __future__ import annotations

"""JSON/JSONL 题库读取工具。"""

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def load_json_or_jsonl(path: Path) -> Tuple[Any, Dict[str, Any]]:
    """读取普通 JSON 或每行一个题目对象的 JSONL。"""

    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError("题库文件为空。")

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
