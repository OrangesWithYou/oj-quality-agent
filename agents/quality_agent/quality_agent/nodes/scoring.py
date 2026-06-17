"""规则评分层。

这里负责把“完整性检查结果 + Solver 实测结果”转成难度、风险标签
和是否进入人工复核队列。LLM 归因只做解释，不替代这里的规则兜底。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List


DIFFICULTY_ORDER = {
    "未评测": 0,
    "简单": 1,
    "中等": 2,
    "困难": 3,
    "超难": 4,
    "疑似坏题": 5,
}


def difficulty_from_pass_rate(pass_rate: float, *, has_error_finding: bool = False) -> str:
    """根据通过率和结构性错误粗分难度。"""

    if has_error_finding and pass_rate <= 0:
        return "疑似坏题"
    if pass_rate >= 0.85:
        return "简单"
    if pass_rate >= 0.60:
        return "中等"
    if pass_rate >= 0.30:
        return "困难"
    if pass_rate > 0:
        return "超难"
    return "超难"


def _problem_id(problem: Dict[str, Any]) -> str:
    """从不同评测摘要格式中统一提取题目 ID。"""

    return str(problem.get("problem_id") or problem.get("case_id") or problem.get("index") or "")


def _finding_index(findings: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """按题目 ID 建索引，方便把完整性问题合并到题目结果上。"""

    result: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        result[str(finding.get("item_id", ""))].append(finding)
    return result


def _risk_flags(
    *,
    pass_rate: float,
    difficulty: str,
    findings: List[Dict[str, Any]],
    eval_problem: Dict[str, Any],
) -> List[str]:
    """根据通过率、完整性问题和评测状态生成风险标签。"""

    flags: List[str] = []
    if pass_rate < 0.30:
        flags.append("low_pass_rate")
    if difficulty == "疑似坏题":
        flags.append("suspect_bad_item")
    if any(item.get("severity") == "error" for item in findings):
        flags.append("completeness_error")
    if any(item.get("severity") == "warning" for item in findings):
        flags.append("completeness_warning")
    if int(eval_problem.get("invalid_samples", 0) or 0) > 0:
        flags.append("invalid_samples")
    if not bool(eval_problem.get("included_in_accuracy", True)):
        flags.append("excluded_from_accuracy")
    return flags


def score_quality(
    *,
    eval_summary: Dict[str, Any],
    completeness_findings: List[Dict[str, Any]],
    dataset_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """把 Solver 证据和结构检查结果合成为质检结论。"""

    by_id = _finding_index(completeness_findings)
    problems = eval_summary.get("problems") or []
    dry_run = bool(eval_summary.get("dry_run"))
    no_solver_evidence = dry_run or not problems
    if not problems:
        # dry-run 或工具失败时可能没有 problem 级结果；这里用原始题目生成占位结果，
        # 保证报告仍能覆盖每一道题。
        problems = [
            {
                "problem_id": str(item.get("problem_id") or item.get("ID") or idx),
                "question_type": item.get("gtupe") or item.get("question_type") or "",
                "sample_count": 0,
                "valid_sample_count": 0,
                "passed_samples": 0,
                "failed_samples": 0,
                "sample_pass_rate": 0.0,
                "included_in_accuracy": False,
                "problem_correct": False,
            }
            for idx, item in enumerate(dataset_items, start=1)
        ]

    results: List[Dict[str, Any]] = []
    for problem in problems:
        pid = _problem_id(problem)
        findings = by_id.get(pid, [])
        pass_rate = float(problem.get("sample_pass_rate", 0.0) or 0.0)
        has_error_finding = any(item.get("severity") == "error" for item in findings)
        if no_solver_evidence:
            # 没有真实模型证据时，不能把 0% 误判成难题。
            difficulty = "未评测"
            flags = ["dry_run_no_solver_evidence"] if dry_run else ["no_solver_evidence"]
            if has_error_finding:
                flags.append("completeness_error")
            elif any(item.get("severity") == "warning" for item in findings):
                flags.append("completeness_warning")
        else:
            difficulty = difficulty_from_pass_rate(pass_rate, has_error_finding=has_error_finding)
            flags = _risk_flags(pass_rate=pass_rate, difficulty=difficulty, findings=findings, eval_problem=problem)

        # 只有 dry_run_no_solver_evidence 不触发复核；其他风险或困难以上都进入复核队列。
        need_review = any(flag != "dry_run_no_solver_evidence" for flag in flags) or DIFFICULTY_ORDER.get(difficulty, 0) >= DIFFICULTY_ORDER["困难"]
        results.append(
            {
                "problem_id": pid,
                "question_type": problem.get("question_type", ""),
                "sample_count": problem.get("sample_count", problem.get("samples", 0)),
                "valid_sample_count": problem.get("valid_sample_count", 0),
                "passed_samples": problem.get("passed_samples", 0),
                "failed_samples": problem.get("failed_samples", 0),
                "sample_pass_rate": pass_rate,
                "agent_difficulty": difficulty,
                "risk_flags": flags,
                "need_human_review": need_review,
                "completeness_findings": findings,
                "evidence": {
                    "included_in_accuracy": problem.get("included_in_accuracy", True),
                    "problem_correct": problem.get("problem_correct", False),
                    "final_status_description": problem.get("final_status_description", ""),
                },
            }
        )
    return results


def score_quality_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """LangGraph 节点：执行规则评分。"""

    results = score_quality(
        eval_summary=state.get("eval_summary", {}),
        completeness_findings=state.get("completeness_findings", []),
        dataset_items=state.get("dataset_items", []),
    )
    return {**state, "quality_results": results}
