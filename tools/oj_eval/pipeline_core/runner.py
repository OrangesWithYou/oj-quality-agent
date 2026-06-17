from __future__ import annotations

"""端到端运行器：调用 PPIO 生成代码 -> Judge0 判题 -> 汇总落盘。"""

import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import requests

from .core import (
    ProblemCase,
    RuntimeConfig,
    SamplingConfig,
    SESSION,
    collect_failed_tests,
    expand_sample_plan,
    extract_code,
    non_empty_text,
    pick_final_sample,
    safe_int,
    write_text,
)
from .core import write_json
from .langfuse_trace import finish_generation, finish_generation_error, trace_generation
from .reporting import (
    build_problem_level_summary,
    build_sample_status_summary,
    is_problem_correct,
    is_valid_sample_status,
    write_combined_latest_report,
    write_run_outputs,
)
from .services import JudgeClient


class PPIOJudgeRunner:
    """端到端运行器：PPIO 生成代码 -> Judge0 判题 -> 汇总落盘。"""

    def __init__(
        self,
        runtime: RuntimeConfig,
        logger: logging.Logger,
        *,
        session: requests.Session = SESSION,
    ):
        self.runtime = runtime
        self.logger = logger
        self.session = session
        self.judge_client = JudgeClient(
            base_url=runtime.judge0_base_url,
            language_id=runtime.judge0_language_id,
            poll_interval_sec=runtime.poll_interval_sec,
            poll_timeout_sec=runtime.poll_timeout_sec,
            logger=logger,
        )

    # ── PPIO 调用 ──────────────────────────────────────────────────────────────

    def _build_payload(self, problem_text: str, sampling: SamplingConfig) -> Dict[str, Any]:
        """构造 PPIO chat/completions 请求载荷。"""
        payload: Dict[str, Any] = {
            "model": self.runtime.ppio_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个只输出可直接提交到 OJ 的 Python 3 代码助手。",
                },
                {
                    "role": "user",
                    "content": (
                        "请为下面代码题生成 Python 3 代码。\n\n"
                        "要求：\n"
                        "1. 只输出代码\n"
                        "2. 不要解释\n"
                        "3. 标准输入输出\n"
                        "4. 代码可直接提交到 OJ\n\n"
                        f"题目：\n{problem_text}\n"
                    ),
                },
            ],
            "stream": False,
            "temperature": sampling.temperature,
            "top_p": sampling.top_p,
            "frequency_penalty": sampling.frequency_penalty,
            "max_tokens": sampling.max_tokens,
        }
        if sampling.top_k is not None:
            payload["top_k"] = sampling.top_k
        return payload

    def _call_ppio(
        self,
        original_payload: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any], bool]:
        """发起 PPIO 请求，返回 (response_data, effective_payload, top_k_effective)。

        若接口不支持 top_k，自动去掉后重试一次。
        """
        url = f"{self.runtime.ppio_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.runtime.ppio_api_key}",
            "Content-Type": "application/json",
        }
        effective_payload = dict(original_payload)
        top_k_effective = "top_k" in effective_payload

        generation = None
        with trace_generation(
            name="oj_eval_code_solver",
            payload=effective_payload,
            metadata={"solver_domain": "code"},
        ) as generation:
            resp = self.session.post(url, headers=headers, json=effective_payload, timeout=180)

            # 兼容处理：若接口不支持 top_k，自动移除后重试。
            if resp.status_code in (400, 422) and "top_k" in effective_payload:
                err_text = (resp.text or "").lower()
                if ("top_k" in err_text or "topk" in err_text) and any(
                    w in err_text for w in ["unsupported", "unknown", "invalid", "not support"]
                ):
                    finish_generation_error(
                        generation,
                        message="接口不支持 top_k，已移除参数后重试。",
                        output={"status_code": resp.status_code, "response": resp.text[:1000]},
                        level="WARNING",
                    )
                    self.logger.warning("接口不支持 top_k，自动去掉后重试。")
                    effective_payload.pop("top_k", None)
                    top_k_effective = False
                    with trace_generation(
                        name="oj_eval_code_solver_retry_without_top_k",
                        payload=effective_payload,
                        metadata={"solver_domain": "code", "retry_reason": "top_k_unsupported"},
                    ) as retry_generation:
                        resp = self.session.post(url, headers=headers, json=effective_payload, timeout=180)
                        resp.raise_for_status()
                        try:
                            data = resp.json()
                        except ValueError:
                            data = {"_raw_text": resp.text}
                        finish_generation(retry_generation, data)
                    return data, effective_payload, top_k_effective

            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                data = {"_raw_text": resp.text}
            finish_generation(generation, data)

        return data, effective_payload, top_k_effective

    def _parse_response(self, data: Dict[str, Any]) -> tuple[str, str, Dict[str, Any]]:
        """从响应中提取 raw_text、code、usage。"""
        raw_text = ""
        choices = data.get("choices") if isinstance(data, dict) else None
        if isinstance(choices, list) and choices:
            message = (choices[0] or {}).get("message") or {}
            raw_text = non_empty_text(message.get("content"))
        code = extract_code(raw_text)
        usage = data.get("usage") if isinstance(data, dict) else {}
        if not isinstance(usage, dict):
            usage = {}
        return raw_text, code, usage

    # ── 单次采样 ───────────────────────────────────────────────────────────────

    def run_single_sample(
        self,
        case: ProblemCase,
        sample_index: int,
        sampling: SamplingConfig,
        sample_dir: Path,
    ) -> Dict[str, Any]:
        """执行单次采样：生成代码、判题并落盘单样本结果。"""
        sample_dir.mkdir(parents=True, exist_ok=True)

        request_original_path = sample_dir / "ppio_request.original.json"
        request_effective_path = sample_dir / "ppio_request.effective.json"
        response_path = sample_dir / "ppio_response.json"
        code_path = sample_dir / "generated_code.py"
        judge_path = sample_dir / "judge_result.json"
        sample_summary_path = sample_dir / "sample_summary.json"

        original_payload = self._build_payload(case.problem_text, sampling)
        write_json(request_original_path, original_payload)

        result: Dict[str, Any] = {
            "sample_index": sample_index,
            "sampling_config": asdict(sampling),
            "request_original_path": str(request_original_path),
            "request_effective_path": str(request_effective_path),
            "response_path": str(response_path),
            "code_path": str(code_path),
            "judge_result_path": str(judge_path),
            "top_k_effective": sampling.top_k is not None,
            "raw_response_text": "",
            "code": "",
            "token": None,
            "usage": {},
            "judge_passed": False,
            "judge_status_id": None,
            "judge_status_description": "NOT_RUN",
            "stdout": None,
            "stderr": None,
            "compile_output": None,
            "failed_tests": [],
            "error": None,
        }

        try:
            # 1) 调用 PPIO 生成代码
            response_data, effective_payload, top_k_effective = self._call_ppio(original_payload)
            result["top_k_effective"] = top_k_effective
            write_json(request_effective_path, effective_payload)
            write_json(response_path, response_data)

            raw_text, code, usage = self._parse_response(response_data)
            if not code:
                raise RuntimeError("模型返回为空，未提取到可提交代码")

            result["raw_response_text"] = raw_text
            result["code"] = code
            result["usage"] = usage
            write_text(code_path, code)

            # 2) 提交 Judge0 判题
            judge_result = self.judge_client.judge(
                source_code=code,
                stdin=case.stdin,
                expected_output=case.expected_output,
            )
            write_json(judge_path, judge_result)

            # 3) 提取判题状态
            status = judge_result.get("status") or {}
            status_id = safe_int(status.get("id"), -1)
            status_desc = non_empty_text(status.get("description")) or "UNKNOWN"
            passed = status_id == 3

            result.update({
                "token": judge_result.get("token"),
                "judge_passed": passed,
                "judge_status_id": status_id,
                "judge_status_description": status_desc,
                "stdout": judge_result.get("stdout"),
                "stderr": judge_result.get("stderr"),
                "compile_output": judge_result.get("compile_output"),
                "failed_tests": collect_failed_tests(judge_result, case.stdin, case.expected_output),
            })

            self.logger.info(
                "Judge | 题目=%s | sample=%02d | status_id=%s | %s | passed=%s | token=%s",
                case.problem_id, sample_index, status_id, status_desc, passed, judge_result.get("token"),
            )

        except Exception as exc:
            self.logger.exception("采样失败 | 题目=%s | sample=%s", case.problem_id, sample_index)
            result["error"] = str(exc)
            if not request_effective_path.exists():
                write_json(request_effective_path, original_payload)
            if not response_path.exists():
                write_json(response_path, {"error": str(exc)})
            if not judge_path.exists():
                write_json(judge_path, {"error": "judge_not_run"})

        write_json(sample_summary_path, result)
        return result

    # ── 单题 ───────────────────────────────────────────────────────────────────

    def run_problem(
        self,
        case: ProblemCase,
        default_sampling: SamplingConfig,
        run_dir: Path,
    ) -> Dict[str, Any]:
        """执行单题全量采样并汇总题目级结果。"""
        problem_dir = run_dir / case.problem_id
        problem_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info("开始处理题目: %s", case.problem_id)

        sample_plan = expand_sample_plan(
            sample_plan=case.sample_plan,
            default_sampling=default_sampling,
            samples_per_problem=self.runtime.samples_per_problem,
        )

        sample_results: List[Dict[str, Any]] = []
        for idx, sampling in enumerate(sample_plan, start=1):
            self.logger.info(
                "题目 %s 第 %d/%d 次采样 | temp=%.3f top_p=%.3f freq_pen=%.3f max_tokens=%d top_k=%s",
                case.problem_id, idx, self.runtime.samples_per_problem,
                sampling.temperature, sampling.top_p, sampling.frequency_penalty,
                sampling.max_tokens, str(sampling.top_k),
            )
            sample_result = self.run_single_sample(case, idx, sampling, problem_dir / f"sample_{idx:02d}")
            sample_results.append(sample_result)

        sample_count = len(sample_results)
        valid_sample_count = sum(1 for s in sample_results if is_valid_sample_status(s))
        pass_count = sum(1 for s in sample_results if is_valid_sample_status(s) and s.get("judge_passed"))
        failed_count = sample_count - pass_count
        pass_rate = pass_count / float(sample_count) if sample_count else 0.0
        included_in_accuracy = sample_count > 0
        problem_correct = included_in_accuracy and is_problem_correct(pass_count, sample_count)
        final_sample = pick_final_sample(sample_results, self.runtime.final_pick_strategy)

        problem_summary: Dict[str, Any] = {
            "problem_id": case.problem_id,
            "metadata": case.metadata,
            "sample_count": sample_count,
            "valid_sample_count": valid_sample_count,
            "passed_samples": pass_count,
            "failed_samples": failed_count,
            "invalid_samples": sample_count - valid_sample_count,
            "sample_pass_rate": pass_rate,
            "included_in_accuracy": included_in_accuracy,
            "problem_correct": problem_correct,
            "is_hard": not problem_correct,
            "final_pick_strategy": self.runtime.final_pick_strategy,
            "final_sample_index": final_sample.get("sample_index"),
            "final_judge_passed": final_sample.get("judge_passed"),
            "final_status_id": final_sample.get("judge_status_id"),
            "final_status_description": final_sample.get("judge_status_description"),
            "final_token": final_sample.get("token"),
            "final_code_path": final_sample.get("code_path"),
            "sample_statuses": [
                {
                    "sample_index": s.get("sample_index"),
                    "valid_sample": is_valid_sample_status(s),
                    "judge_passed": s.get("judge_passed"),
                    "judge_status_id": s.get("judge_status_id"),
                    "judge_status_description": s.get("judge_status_description"),
                    "token": s.get("token"),
                }
                for s in sample_results
            ],
        }

        write_json(problem_dir / "problem_summary.json", problem_summary)
        return problem_summary

    # ── 整批 ───────────────────────────────────────────────────────────────────

    def run(self, cases: List[ProblemCase], default_sampling: SamplingConfig, run_dir: Path) -> Dict[str, Any]:
        """执行整批题目并输出运行级汇总。"""
        started_at = datetime.now().isoformat()
        problem_results: List[Dict[str, Any]] = []

        for idx, case in enumerate(cases, start=1):
            self.logger.info("进度 %d/%d | %s", idx, len(cases), case.problem_id)
            problem_results.append(self.run_problem(case, default_sampling, run_dir))

        hard_problems = [
            {
                "problem_id": p["problem_id"],
                "sample_count": p["sample_count"],
                "valid_sample_count": p["valid_sample_count"],
                "passed_samples": p["passed_samples"],
                "failed_samples": p["failed_samples"],
                "included_in_accuracy": p["included_in_accuracy"],
                "sample_pass_rate": p["sample_pass_rate"],
                "problem_correct": p["problem_correct"],
                "final_status_id": p["final_status_id"],
                "final_status_description": p["final_status_description"],
            }
            for p in problem_results if p.get("is_hard")
        ]

        all_sample_statuses = [
            sample
            for problem in problem_results
            for sample in problem.get("sample_statuses", [])
        ]
        sample_status_summary = build_sample_status_summary(all_sample_statuses)
        judge_status_summary = {
            "total_samples": sample_status_summary["sample_count"],
            "valid_samples": sample_status_summary["valid_samples"],
            "invalid_samples": sample_status_summary["invalid_samples"],
            "passed_samples": sample_status_summary["passed_samples"],
            "failed_samples": sample_status_summary["failed_samples"],
            "sample_pass_rate": sample_status_summary["sample_pass_rate"],
            "status_distribution": sample_status_summary["status_distribution"],
        }
        problem_status_summary = build_problem_level_summary(problem_results)

        run_summary: Dict[str, Any] = {
            "mode": "code",
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(),
            "model": self.runtime.ppio_model,
            "judge0_base_url": self.runtime.judge0_base_url,
            "samples_per_problem": self.runtime.samples_per_problem,
            "final_pick_strategy": self.runtime.final_pick_strategy,
            "judgment_rule": problem_status_summary["judgment_rule"],
            "total_problems": problem_status_summary["total_problems"],
            "correct_problem_count": problem_status_summary["correct_problem_count"],
            "wrong_problem_count": problem_status_summary["wrong_problem_count"],
            "problem_accuracy": problem_status_summary["problem_accuracy"],
            "problem_wrong_rate": problem_status_summary["problem_wrong_rate"],
            "hard_problem_count": len(hard_problems),
            "hard_problem_ids": [p["problem_id"] for p in hard_problems],
            "problem_status_summary": problem_status_summary,
            "judge_status_summary": judge_status_summary,
            "problems": problem_results,
        }

        write_run_outputs(run_dir, run_summary)
        write_combined_latest_report()
        write_json(run_dir / "hard_problems.json", hard_problems)
        write_json(run_dir / "log_status_summary.json", judge_status_summary)

        self.logger.info(
            "汇总 | total_samples=%s passed=%s failed=%s pass_rate=%.3f",
            judge_status_summary["total_samples"],
            judge_status_summary["passed_samples"],
            judge_status_summary["failed_samples"],
            judge_status_summary["sample_pass_rate"],
        )
        self.logger.info("运行结束 -> %s", run_dir / "run_summary.json")
        return run_summary
