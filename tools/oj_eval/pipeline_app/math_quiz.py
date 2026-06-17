from __future__ import annotations

"""数学客观题测验模块（与代码生成评测流程隔离）。

职责：
- 读取 `cases_math.json`（或同结构 JSON）
- 按题型过滤（判断 / 单选 / 多选）
- 调用大模型作答并自动判分
- 输出分题型统计与运行摘要
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from pipeline_core import SESSION, non_empty_text, write_json
from pipeline_core.langfuse_trace import finish_generation, finish_generation_error, trace_generation
from pipeline_core.reporting import (
    build_problem_level_summary,
    build_sample_status_summary,
    is_problem_correct,
    is_valid_sample_status,
    write_combined_latest_report,
    write_run_outputs,
)
from pipeline_app.json_loader import load_json_or_jsonl


SUPPORTED_QUESTION_TYPES: Tuple[str, ...] = ("判断", "单选", "多选")


QUESTION_TYPE_ALIASES: Dict[str, str] = {
    "判断": "判断",
    "判断题": "判断",
    "judgechoice": "判断",
    "truefalse": "判断",
    "tf": "判断",
    "单选": "单选",
    "单选题": "单选",
    "single": "单选",
    "singlechoice": "单选",
    "多选": "多选",
    "多选题": "多选",
    "multiple": "多选",
    "multiplechoice": "多选",
}


@dataclass
class MathQuizCase:
    """数学客观题结构。"""

    case_id: str
    question_type: str
    question: str
    choices: str
    answer: str
    raw: Dict[str, Any]


@dataclass
class MathQuizRuntimeConfig:
    """数学客观题测验运行配置。"""

    ppio_api_key: str
    ppio_base_url: str
    ppio_model: str
    question_types: List[str]
    max_cases: int = 0
    samples: int = 4          # 每题独立采样次数（与代码题 --samples 对齐）
    request_interval: float = 1  # 每次 API 请求后的等待秒数，避免限流
    temperature: float = 0.2
    top_p: float = 0.95
    max_tokens: int = 256


def _normalize_key(value: str) -> str:
    """标准化文本键，用于别名匹配。"""

    return re.sub(r"[\s_\-:：,，;；]+", "", value.strip().lower())


def normalize_question_type(value: Any) -> str:
    """将外部题型文本归一化到：判断 / 单选 / 多选。"""

    text = non_empty_text(value)
    if not text:
        return ""

    key = _normalize_key(text)
    if key in QUESTION_TYPE_ALIASES:
        return QUESTION_TYPE_ALIASES[key]

    if "判断" in text:
        return "判断"
    if "单选" in text:
        return "单选"
    if "多选" in text:
        return "多选"
    return ""


def normalize_choices(value: Any) -> str:
    """把标准 options 或旧 choices 字段统一成模型可读的选项文本。"""

    if isinstance(value, list):
        lines: List[str] = []
        for idx, item in enumerate(value):
            default_label = chr(ord("A") + idx)
            if isinstance(item, dict):
                label = non_empty_text(item.get("label") or item.get("key") or item.get("id") or default_label)
                text = non_empty_text(item.get("text") or item.get("value") or item.get("content") or item.get("name"))
                lines.append(f"{label or default_label}. {text}")
            else:
                text = non_empty_text(item)
                if text:
                    lines.append(text)
        return "\n".join(lines)
    raw_choices = non_empty_text(value)
    return "" if raw_choices.lower() == "null" else raw_choices


def parse_question_types(value: str) -> List[str]:
    """解析题型字符串（逗号分隔）并保持输入顺序。"""

    parts = [x.strip() for x in re.split(r"[,，;；|]", non_empty_text(value)) if x.strip()]
    result: List[str] = []
    for item in parts:
        normalized = normalize_question_type(item)
        if normalized in SUPPORTED_QUESTION_TYPES and normalized not in result:
            result.append(normalized)

    if not result:
        # 入参示例："判断,单选,多选"；若一个都识别不到则提示合法值。
        raise ValueError(
            f"未识别到有效题型，请使用：{','.join(SUPPORTED_QUESTION_TYPES)}，例如：判断,单选,多选"
        )
    return result


def parse_math_ids(value: str) -> List[str]:
    """解析 --math-ids 参数，返回 ID 字符串列表（逗号分隔）。"""

    parts = [x.strip() for x in re.split(r"[,，;；]", value.strip()) if x.strip()]
    return parts


def load_math_quiz_cases(
    cases_file: Path,
    question_types: List[str],
    max_cases: int = 0,
    ids: Optional[List[str]] = None,
) -> List[MathQuizCase]:
    """从 JSON/JSONL 载入数学客观题，支持按题型过滤和按 ID 抽样。

    ids 不为空时：只保留 ID 在列表中的题目（忽略 question_types 过滤和 max_cases 限制）。
    ids 为空时：按 question_types 过滤，再按 max_cases 截断。
    """

    if not cases_file.exists():
        raise FileNotFoundError(f"数学题库文件不存在: {cases_file}")

    raw, _ = load_json_or_jsonl(cases_file)
    if isinstance(raw, dict):
        items = raw.get("cases") or raw.get("questions") or []
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("数学题库格式错误：应为数组、{'cases': [...]} 或 JSONL")

    if not isinstance(items, list):
        raise ValueError("数学题库格式错误：cases 必须是数组")

    # 按 ID 抽样模式：直接按 ID 集合过滤，保持文件中的原始顺序
    id_set: Optional[set] = set(ids) if ids else None
    allowed_question_types = {
        normalized
        for raw_type in question_types
        for normalized in [normalize_question_type(raw_type)]
        if normalized
    }

    selected: List[MathQuizCase] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue

        case_id = non_empty_text(item.get("ID") or item.get("id") or item.get("problem_id")) or f"{idx}"

        if id_set is not None:
            # ID 抽样模式：只要 ID 匹配即可，不做题型过滤
            if case_id not in id_set:
                continue
        else:
            # 普通模式：按题型过滤
            question_type = normalize_question_type(
                item.get("gtupe")
                or item.get("question_type")
                or item.get("questionType")
                or item.get("type")
                or item.get("题型")
            )
            if question_type not in allowed_question_types:
                continue

        question_type = normalize_question_type(
            item.get("gtupe")
            or item.get("question_type")
            or item.get("questionType")
            or item.get("type")
            or item.get("题型")
        )

        question = non_empty_text(item.get("question") or item.get("problem_text"))
        if not question:
            continue

        choices = normalize_choices(item.get("choices") if item.get("choices") is not None else item.get("options"))
        answer = non_empty_text(item.get("answer"))

        selected.append(
            MathQuizCase(
                case_id=case_id,
                question_type=question_type,
                question=question,
                choices=choices,
                answer=answer,
                raw=item,
            )
        )

        if id_set is None and max_cases > 0 and len(selected) >= max_cases:
            break

    return selected


def _normalize_single_choice(text: str) -> str:
    """规范化单选答案：提取首个大写字母。"""

    letters = re.findall(r"[A-Z]", non_empty_text(text).upper())
    return letters[0] if letters else ""


def _normalize_multi_choice(text: str) -> str:
    """规范化多选答案：提取字母并按字母序去重。"""

    letters = re.findall(r"[A-Z]", non_empty_text(text).upper())
    return "".join(sorted(set(letters))) if letters else ""


def _normalize_judgement(text: str) -> str:
    """规范化判断题答案：统一为“正确 / 错误”。"""

    raw = non_empty_text(text)
    if not raw:
        return ""

    lowered = raw.lower().strip()
    if lowered in {"正确", "对", "true", "t", "yes", "y", "1", "√"}:
        return "正确"
    if lowered in {"错误", "错", "false", "f", "no", "n", "0", "×"}:
        return "错误"

    candidates: List[Tuple[int, str]] = []
    for token in ["正确", "对", "true", "√"]:
        idx = lowered.find(token)
        if idx >= 0:
            candidates.append((idx, "正确"))
    for token in ["错误", "错", "false", "×"]:
        idx = lowered.find(token)
        if idx >= 0:
            candidates.append((idx, "错误"))

    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def normalize_answer_by_type(question_type: str, text: str) -> str:
    """按题型规范化答案文本。"""

    # 规则说明：
    # - 判断题：归一化为“正确/错误”
    # - 单选题：取首个字母（A/B/C/D...）
    # - 多选题：提取全部字母并去重后按字母序（如 "DACD" -> "ACD"）

    if question_type == "判断":
        return _normalize_judgement(text)
    if question_type == "单选":
        return _normalize_single_choice(text)
    if question_type == "多选":
        return _normalize_multi_choice(text)
    return non_empty_text(text)


def build_math_quiz_prompt(case: MathQuizCase) -> str:
    """构造客观题测验 Prompt。"""

    if case.question_type == "判断":
        answer_format = "请只输出“正确”或“错误”其中一个词，不要输出其他内容。"
    elif case.question_type == "单选":
        answer_format = "请只输出一个大写字母选项（如 A），不要输出解释。"
    else:  # 多选
        answer_format = "请只输出所有正确选项的大写字母组合（如 ACD，按字母升序且不加分隔符）。"

    parts = [
        "请回答下面的数学客观题。",
        f"题型：{case.question_type}",
        f"题目：{case.question}",
    ]
    if case.choices:
        parts.append(f"选项：\n{case.choices}")
    parts.append(answer_format)
    return "\n".join(parts)


class MathQuizRunner:
    """数学客观题测验运行器。"""

    def __init__(
        self,
        runtime: MathQuizRuntimeConfig,
        logger: logging.Logger,
        *,
        session: requests.Session = SESSION,
    ):
        self.runtime = runtime
        self.logger = logger
        self.session = session

    def _call_model(self, prompt: str) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
        """调用 PPIO 模型，返回原始文本、响应 JSON、请求载荷。
        
        遇到 429 限流时自动指数退避重试（最多 5 次，初始等待 5 秒）。
        """

        if not self.runtime.ppio_api_key:
            raise RuntimeError("未提供 PPIO API Key，请使用 --ppio-api-key 或设置环境变量 PPIO_API_KEY")

        payload: Dict[str, Any] = {
            "model": self.runtime.ppio_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是数学客观题答题助手。必须严格按用户要求格式，只输出最终答案。",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "stream": False,
            "temperature": self.runtime.temperature,
            "top_p": self.runtime.top_p,
            "max_tokens": self.runtime.max_tokens,
        }

        headers = {
            "Authorization": f"Bearer {self.runtime.ppio_api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.runtime.ppio_base_url}/chat/completions"

        max_retries = 5
        wait = 5.0  # 初始等待秒数
        for attempt in range(1, max_retries + 1):
            generation = None
            try:
                with trace_generation(
                    name="oj_eval_math_solver",
                    payload=payload,
                    metadata={"solver_domain": "math", "attempt": attempt},
                ) as generation:
                    resp = self.session.post(url, headers=headers, json=payload, timeout=180)

                    if resp.status_code == 429:
                        # 优先读取 Retry-After 头
                        retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                        sleep_sec = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else wait
                        if attempt >= max_retries:
                            resp.raise_for_status()
                        finish_generation_error(
                            generation,
                            message=f"429 限流，等待 {sleep_sec:.1f} 秒后重试。",
                            output={"status_code": 429, "retry_after": sleep_sec},
                            level="WARNING",
                        )
                        self.logger.warning(
                            "429 限流，第 %d/%d 次重试，等待 %.1f 秒...", attempt, max_retries, sleep_sec
                        )
                        time.sleep(sleep_sec)
                        wait = min(wait * 2, 60.0)
                        continue

                    resp.raise_for_status()
                    data = resp.json()
                    finish_generation(generation, data)
            except Exception:
                # RetryError / ConnectionError 等：直接走退避
                if attempt >= max_retries:
                    raise
                self.logger.warning("请求异常，第 %d 次重试，等待 %.1f 秒...", attempt, wait)
                time.sleep(wait)
                wait = min(wait * 2, 60.0)
                continue

            raw_text = ""
            choices = data.get("choices") if isinstance(data, dict) else None
            if isinstance(choices, list) and choices:
                message = (choices[0] or {}).get("message") or {}
                raw_text = non_empty_text(message.get("content"))

            return raw_text, data, payload

        # 不应到达此处
        raise RuntimeError("_call_model: 超过最大重试次数")

    @staticmethod
    def _safe_name(text: str) -> str:
        """将任意文本转为安全目录名片段。"""

        cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5_-]+", "_", non_empty_text(text))
        return cleaned.strip("_") or "item"

    def _run_single_sample(
        self,
        case: MathQuizCase,
        index: int,
        sample_no: int,
        case_dir: Path,
        prompt: str,
        gold_answer_normalized: str,
    ) -> Dict[str, Any]:
        """执行单题单次采样并落盘到 sample_XX/ 子目录。"""

        sample_dir = case_dir / f"sample_{sample_no:02d}"
        sample_dir.mkdir(parents=True, exist_ok=True)

        sample_result: Dict[str, Any] = {
            "sample_no": sample_no,
            "case_id": case.case_id,
            "question_type": case.question_type,
            "gold_answer_normalized": gold_answer_normalized,
            "model_answer_raw": "",
            "model_answer_normalized": "",
            "is_correct": False,
            "error": None,
        }

        try:
            raw_answer, response_data, request_payload = self._call_model(prompt)
            write_json(sample_dir / "ppio_request.json", request_payload)
            write_json(sample_dir / "ppio_response.json", response_data)

            model_answer = normalize_answer_by_type(case.question_type, raw_answer)
            is_correct = bool(model_answer) and model_answer == gold_answer_normalized

            sample_result["model_answer_raw"] = raw_answer
            sample_result["model_answer_normalized"] = model_answer
            sample_result["is_correct"] = is_correct

            self.logger.info(
                "数学测验 | idx=%s | case_id=%s | sample=%s | type=%s | gold=%s | pred=%s | correct=%s",
                index,
                case.case_id,
                sample_no,
                case.question_type,
                gold_answer_normalized,
                model_answer,
                is_correct,
            )
        except Exception as exc:
            sample_result["error"] = str(exc)
            self.logger.exception(
                "数学测验失败 | idx=%s | case_id=%s | sample=%s", index, case.case_id, sample_no
            )

        write_json(sample_dir / "sample_result.json", sample_result)
        # 每次请求后等待，避免触发 API 限流（request_interval=0 时跳过）
        if self.runtime.request_interval > 0:
            time.sleep(self.runtime.request_interval)
        return sample_result

    def run_single_case(self, case: MathQuizCase, index: int, run_dir: Path) -> Dict[str, Any]:
        """执行单题多次采样测验并落盘。"""

        case_dir = run_dir / "questions" / f"{index:03d}_{self._safe_name(case.question_type)}_{self._safe_name(case.case_id)}"
        case_dir.mkdir(parents=True, exist_ok=True)

        prompt = build_math_quiz_prompt(case)
        gold_answer_normalized = normalize_answer_by_type(case.question_type, case.answer)
        samples = max(1, self.runtime.samples)

        sample_results: List[Dict[str, Any]] = []
        for s in range(1, samples + 1):
            sample_results.append(
                self._run_single_sample(case, index, s, case_dir, prompt, gold_answer_normalized)
            )

        valid_sample_count = sum(1 for s in sample_results if is_valid_sample_status(s))
        correct_count = sum(1 for s in sample_results if is_valid_sample_status(s) and s.get("is_correct"))
        wrong_count = samples - correct_count
        any_correct = correct_count > 0
        included_in_accuracy = samples > 0
        majority_correct = included_in_accuracy and is_problem_correct(correct_count, samples)

        result: Dict[str, Any] = {
            "index": index,
            "case_id": case.case_id,
            "question_type": case.question_type,
            "question": case.question,
            "choices": case.choices,
            "gold_answer_raw": case.answer,
            "gold_answer_normalized": gold_answer_normalized,
            "sample_count": samples,
            "valid_sample_count": valid_sample_count,
            "samples": samples,
            "passed_samples": correct_count,
            "correct_count": correct_count,
            "failed_samples": wrong_count,
            "invalid_samples": samples - valid_sample_count,
            "sample_pass_rate": correct_count / float(samples) if samples else 0.0,
            "included_in_accuracy": included_in_accuracy,
            "problem_correct": majority_correct,
            "any_correct": any_correct,
            "majority_correct": majority_correct,
            "sample_results": sample_results,
        }

        write_json(case_dir / "question_result.json", result)
        return result

    def _build_summary(
        self, cases_file: Path, run_dir: Path, results: List[Dict[str, Any]], started_at: str
    ) -> Dict[str, Any]:
        """构建运行级摘要（总体 + 分题型）。

        字段说明：
        - total_questions: 参与测验总题数
        - samples_per_question: 每题采样次数
        - any_correct_questions: 至少一次采样答对的题数（pass@k 分子）
        - majority_correct_questions: 多数采样答对的题数
        - pass_at_k: any_correct_questions / total_questions
        - by_question_type: 各题型分别统计
        """

        total = len(results)
        samples = self.runtime.samples
        any_correct = sum(1 for item in results if item.get("any_correct"))
        majority_correct = sum(1 for item in results if item.get("problem_correct"))
        total_samples = sum(int(item.get("sample_count", 0) or 0) for item in results)
        passed_samples = sum(int(item.get("passed_samples", 0) or 0) for item in results)
        valid_samples = sum(int(item.get("valid_sample_count", 0) or 0) for item in results)

        # 收集所有题型（含 ID 抽样模式下可能跨题型的情况）
        all_types = list(dict.fromkeys(
            item.get("question_type", "") for item in results
        ))

        by_type: Dict[str, Dict[str, Any]] = {}
        for question_type in all_types:
            one_type = [item for item in results if item.get("question_type") == question_type]
            one_total = len(one_type)
            one_any = sum(1 for item in one_type if item.get("any_correct"))
            one_majority = sum(1 for item in one_type if item.get("problem_correct"))
            by_type[question_type] = {
                "total": one_total,
                "any_correct": one_any,
                "majority_correct": one_majority,
                "pass_at_k": (one_any / float(one_total)) if one_total else 0.0,
                "problem_accuracy": (one_majority / float(one_total)) if one_total else 0.0,
            }

        problems = [
            {
                "problem_id": item.get("case_id"),
                "index": item.get("index"),
                "question_type": item.get("question_type"),
                "sample_count": item.get("sample_count", 0),
                "valid_sample_count": item.get("valid_sample_count", 0),
                "passed_samples": item.get("passed_samples", 0),
                "failed_samples": item.get("failed_samples", 0),
                "invalid_samples": item.get("invalid_samples", 0),
                "sample_pass_rate": item.get("sample_pass_rate", 0.0),
                "included_in_accuracy": item.get("included_in_accuracy", True),
                "problem_correct": item.get("problem_correct", False),
                "judge_status_description": "Accepted" if item.get("problem_correct") else "Wrong Answer",
                "sample_statuses": [
                    {
                        "sample_index": sample.get("sample_no"),
                        "valid_sample": is_valid_sample_status(sample),
                        "judge_passed": sample.get("is_correct"),
                        "judge_status_id": 3 if sample.get("is_correct") else 4,
                        "judge_status_description": (
                            "Accepted" if sample.get("is_correct")
                            else "ERROR" if sample.get("error")
                            else "Wrong Answer"
                        ),
                    }
                    for sample in item.get("sample_results", [])
                ],
            }
            for item in results
        ]
        problem_status_summary = build_problem_level_summary(problems)
        sample_status_summary = build_sample_status_summary(
            sample
            for problem in problems
            for sample in problem.get("sample_statuses", [])
        )
        judge_status_summary = {
            "total_samples": total_samples,
            "valid_samples": valid_samples,
            "invalid_samples": total_samples - valid_samples,
            "passed_samples": passed_samples,
            "failed_samples": total_samples - passed_samples,
            "sample_pass_rate": (passed_samples / float(total_samples)) if total_samples else 0.0,
            "status_distribution": sample_status_summary["status_distribution"],
        }

        return {
            "mode": "math_quiz",
            "cases_file": str(cases_file),
            "run_dir": str(run_dir),
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(),
            "model": self.runtime.ppio_model,
            "question_types": self.runtime.question_types,
            "samples_per_question": samples,
            "max_cases": self.runtime.max_cases,
            "judgment_rule": problem_status_summary["judgment_rule"],
            "total_questions": total,
            "total_problems": problem_status_summary["total_problems"],
            "correct_problem_count": problem_status_summary["correct_problem_count"],
            "wrong_problem_count": problem_status_summary["wrong_problem_count"],
            "problem_accuracy": problem_status_summary["problem_accuracy"],
            "problem_wrong_rate": problem_status_summary["problem_wrong_rate"],
            "any_correct_questions": any_correct,
            "majority_correct_questions": majority_correct,
            "pass_at_k": (any_correct / float(total)) if total else 0.0,
            "majority_accuracy": (majority_correct / float(total)) if total else 0.0,
            "problem_status_summary": problem_status_summary,
            "judge_status_summary": judge_status_summary,
            "by_question_type": by_type,
            "problems": problems,
        }

    def run(
        self,
        cases_file: Path,
        run_dir: Path,
        ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """执行数学客观题测验并输出汇总。

        ids 不为空时按 ID 列表抽样题目，否则按题型过滤。
        """

        cases = load_math_quiz_cases(
            cases_file=cases_file,
            question_types=self.runtime.question_types,
            max_cases=self.runtime.max_cases,
            ids=ids,
        )
        if not cases:
            raise RuntimeError(
                f"未在 {cases_file} 中找到可测验题目，请检查题型过滤或 --math-ids 参数"
            )

        self.logger.info(
            "数学测验启动 | cases=%s | total=%s | samples=%s | types=%s",
            cases_file,
            len(cases),
            self.runtime.samples,
            ",".join(self.runtime.question_types),
        )

        started_at = datetime.now().isoformat()
        results: List[Dict[str, Any]] = []
        for idx, case in enumerate(cases, start=1):
            results.append(self.run_single_case(case, idx, run_dir))

        write_json(run_dir / "math_quiz_results.json", results)
        with (run_dir / "math_quiz_results.jsonl").open("w", encoding="utf-8") as f:
            for item in results:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

        summary = self._build_summary(cases_file, run_dir, results, started_at)
        write_run_outputs(run_dir, summary)
        write_combined_latest_report()
        return summary
