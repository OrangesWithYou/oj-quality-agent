"""字段映射模板保存与复用。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def _template_file(project_root: Path | None = None) -> Path:
    root = project_root or Path.cwd()
    return root / ".streamlit" / "field_mapping_templates.json"


def load_mapping_templates(project_root: Path | None = None) -> Dict[str, Any]:
    """读取全部字段映射模板。"""

    path = _template_file(project_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_mapping_template(name: str, mapping: Dict[str, str], project_root: Path | None = None) -> Path:
    """保存一个可复用字段映射模板。"""

    clean_name = str(name or "").strip() or "default"
    path = _template_file(project_root)
    data = load_mapping_templates(project_root)
    data[clean_name] = {
        "updated_at": datetime.now().isoformat(),
        "mapping": {key: value for key, value in mapping.items() if value},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
