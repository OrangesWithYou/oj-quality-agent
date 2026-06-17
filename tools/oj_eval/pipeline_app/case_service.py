from __future__ import annotations

# 题目数据服务模块：负责“题目从哪里来、如何转成统一结构”。
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# 复用核心层的数据结构与 Excel 读取能力。
from pipeline_core import ProblemCase, SamplingConfig, expand_sample_plan, load_problems_from_excel
from pipeline_app.json_loader import load_json_or_jsonl


# 统一内置演示题库：用于 demo 模式或首次初始化 cases 文件。
DEFAULT_DEMO_CASES: Dict[str, Any] = {
    "cases": [
        {
            "problem_id": "new_easy_sum_two_ints",
            "problem_text": (
                "Title: Sum Two Integers\n"
                "Task: Read two integers a and b from standard input, and output a+b.\n"
                "Input: One line with two integers.\n"
                "Output: One integer followed by newline."
            ),
            "stdin": "-7 10\n",
            "expected_output": "3\n",
            "sample_plan": [
                {"temperature": 0.10, "top_p": 0.90, "frequency_penalty": 0.0, "top_k": 64},
                {"temperature": 0.20, "top_p": 0.90, "frequency_penalty": 0.0, "top_k": 64},
                {"temperature": 0.30, "top_p": 0.95, "frequency_penalty": 0.0, "top_k": 64},
                {"temperature": 0.40, "top_p": 0.95, "frequency_penalty": 0.0, "top_k": 64},
            ],
            "metadata": {"source": "new_demo", "difficulty": "easy"},
        },
        {
            "problem_id": "new_easy_multiline_pair_sum",
            "problem_text": (
                "Title: Pair Sum Until EOF\n"
                "Task: Each line contains two integers x and y.\n"
                "For each non-empty line, output x+y on its own line.\n"
                "Read until EOF.\n"
                "Do not print extra text."
            ),
            "stdin": "1 2\n10 -3\n0 0\n",
            "expected_output": "3\n7\n0\n",
            "sample_plan": [
                {"temperature": 0.10, "top_p": 0.90, "frequency_penalty": 0.0, "top_k": 64},
                {"temperature": 0.20, "top_p": 0.90, "frequency_penalty": 0.0, "top_k": 64},
                {"temperature": 0.30, "top_p": 0.95, "frequency_penalty": 0.0, "top_k": 64},
                {"temperature": 0.40, "top_p": 0.95, "frequency_penalty": 0.0, "top_k": 64},
            ],
            "metadata": {"source": "new_demo", "difficulty": "easy"},
        },
        {
            "problem_id": "new_easy_max_of_three",
            "problem_text": (
                "Title: Max of Three\n"
                "Task: Read three integers a b c from one line and print the maximum value.\n"
                "Input: one line, three integers.\n"
                "Output: one integer with newline."
            ),
            "stdin": "5 9 -1\n",
            "expected_output": "9\n",
            "sample_plan": [
                {"temperature": 0.10, "top_p": 0.90, "frequency_penalty": 0.0, "top_k": 64},
                {"temperature": 0.20, "top_p": 0.90, "frequency_penalty": 0.0, "top_k": 64},
                {"temperature": 0.30, "top_p": 0.95, "frequency_penalty": 0.0, "top_k": 64},
                {"temperature": 0.40, "top_p": 0.95, "frequency_penalty": 0.0, "top_k": 64},
            ],
            "metadata": {"source": "new_demo", "difficulty": "easy"},
        },
    ]
}


def _safe_float(value: Any, default: float) -> float:
    """安全转换为 float，失败时回退默认值。"""

    # 空值直接回退默认值。
    if value in (None, ""):
        return default
    try:
        # 正常可转换时返回转换结果。
        return float(value)
    except Exception:
        # 转换失败时兜底默认值，避免流程中断。
        return default


def _safe_int(value: Any, default: Optional[int]) -> Optional[int]:
    """安全转换为 int，失败时回退默认值。"""

    # 空值直接回退默认值。
    if value in (None, ""):
        return default
    try:
        # 先 float 再 int，兼容 Excel 中 64.0 这类值。
        return int(float(value))
    except Exception:
        # 转换失败时兜底默认值。
        return default


class CaseService:
    """题目数据输入服务：负责 JSON/Excel 的读取与导出。"""

    def __init__(self, default_sampling: SamplingConfig, samples_per_problem: int):
        # 记录默认采样参数，用于补齐每题的 sample_plan。
        self.default_sampling = default_sampling
        # 记录每题采样次数，用于统一扩展计划长度。
        self.samples_per_problem = samples_per_problem

    def _sampling_from_dict(self, data: Dict[str, Any]) -> SamplingConfig:
        """把字典配置转成 SamplingConfig，并做容错转换。"""

        return SamplingConfig(
            temperature=_safe_float(data.get("temperature"), self.default_sampling.temperature),
            top_p=_safe_float(data.get("top_p"), self.default_sampling.top_p),
            frequency_penalty=_safe_float(data.get("frequency_penalty"), self.default_sampling.frequency_penalty),
            max_tokens=_safe_int(data.get("max_tokens"), self.default_sampling.max_tokens) or self.default_sampling.max_tokens,
            top_k=_safe_int(data.get("top_k"), self.default_sampling.top_k),
        )

    def ensure_demo_cases_file(self, cases_file: Path, regen: bool) -> None:
        """在 demo 模式下自动初始化题库文件。"""

        # 当要求重建，或文件不存在时，写入内置 demo 题库。
        if regen or not cases_file.exists():
            # 确保目录已创建。
            cases_file.parent.mkdir(parents=True, exist_ok=True)
            # 将 demo 题库存成标准 JSON 结构。
            cases_file.write_text(json.dumps(DEFAULT_DEMO_CASES, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_cases_from_json(self, cases_file: Path) -> List[ProblemCase]:
        """从 JSON 文件读取题目，并标准化为 ProblemCase 列表。"""

        # 1) 基础存在性检查。
        if not cases_file.exists():
            raise FileNotFoundError(f"题目文件不存在: {cases_file}")

        # 2) 解析 JSON/JSONL 内容，兼容 {"cases": [...]} / {"questions": [...]} / [...] / JSONL。
        raw, _ = load_json_or_jsonl(cases_file)
        if isinstance(raw, dict):
            case_items = raw.get("cases") or raw.get("questions") or []
        elif isinstance(raw, list):
            case_items = raw
        else:
            raise ValueError("题目文件格式错误：应为 {'cases': [...]}、[...] 或 JSONL")

        # 3) 确保 cases 是数组。
        if not isinstance(case_items, list):
            raise ValueError("题目文件格式错误：cases 必须是数组")

        # 4) 逐题做结构校验与标准化。
        cases: List[ProblemCase] = []
        for idx, item in enumerate(case_items, start=1):
            # 每个题目必须是对象。
            if not isinstance(item, dict):
                raise ValueError(f"第 {idx} 个题目配置不是对象")

            # 题干为必填字段。
            problem_text = str(item.get("problem_text") or item.get("question") or "").strip()
            if not problem_text:
                raise ValueError(f"第 {idx} 个题目缺少 problem_text/question")

            # 读取并转换该题的采样计划。
            sample_plan_raw = item.get("sample_plan") or []
            sample_plan: List[SamplingConfig] = []
            if isinstance(sample_plan_raw, list):
                for one in sample_plan_raw:
                    if isinstance(one, dict):
                        sample_plan.append(self._sampling_from_dict(one))

            # metadata 允许为空，缺省时使用空字典。
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}

            # 组装统一题目对象，并补齐样本数量。
            case = ProblemCase(
                problem_id=str(item.get("problem_id") or item.get("id") or f"problem_{idx:03d}"),
                problem_text=problem_text,
                stdin=str(item.get("stdin") or ""),
                expected_output=(
                    str(item["expected_output"])
                    if item.get("expected_output") is not None
                    else (str(item["answer"]) if item.get("answer") is not None else None)
                ),
                sample_plan=expand_sample_plan(sample_plan, self.default_sampling, self.samples_per_problem),
                metadata={"source": "cases_file", **metadata},
            )
            cases.append(case)

        # 5) 返回标准化后的题目集合。
        return cases

    def export_first_problem_from_excel(self, excel_path: Path, cases_file: Path) -> None:
        """从 Excel 读取首题并导出到 JSON（仅导出，不执行评测）。"""

        # 使用较宽松的导出采样默认值，避免导出阶段受 max_tokens 影响。
        export_sampling = SamplingConfig(max_tokens=32768)

        # 只读取第一题，作为快速初始化题库的入口。
        excel_cases = load_problems_from_excel(excel_path, export_sampling, samples_per_problem=4, limit=1)
        if not excel_cases:
            raise RuntimeError("Excel 中未读取到题目")

        # 取出第一题并准备 sample_plan（最多 4 条）。
        case = excel_cases[0]
        sample_plan = [
            {
                "temperature": cfg.temperature,
                "top_p": cfg.top_p,
                "frequency_penalty": cfg.frequency_penalty,
                "top_k": cfg.top_k,
            }
            for cfg in case.sample_plan[:4]
        ]

        # 组装导出 JSON 结构，便于后续直接增量补题。
        payload = {
            "cases": [
                {
                    "problem_id": "excel_problem_001",
                    "problem_text": case.problem_text,
                    "stdin": case.stdin,
                    "expected_output": case.expected_output,
                    "sample_plan": sample_plan,
                    "metadata": {
                        "from": str(excel_path),
                        "note": "你可以继续在这个文件里增加更多题目",
                    },
                }
            ]
        }

        # 确保目录存在并写入导出文件。
        cases_file.parent.mkdir(parents=True, exist_ok=True)
        cases_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
