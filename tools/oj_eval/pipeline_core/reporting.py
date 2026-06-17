from __future__ import annotations

"""Run-level summary and Markdown report helpers."""

import json
import html
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .core import non_empty_text, safe_int, write_json, write_text


MAJORITY_RULE_DESCRIPTION = "单题全部采样中作对比例大于等于 50% 算正确；采样失败按未通过计入分母"


def _rate(numerator: int, denominator: int) -> float:
    return (numerator / float(denominator)) if denominator else 0.0


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _mode_label(mode: str) -> str:
    if mode == "code":
        return "代码题"
    if mode == "math_quiz":
        return "数学题"
    return mode or "未知"


def _samples_per_problem(summary: Dict[str, Any]) -> Any:
    return (
        summary.get("samples_per_problem")
        or summary.get("samples_per_question")
        or summary.get("每题采样次数")
        or ""
    )


def _format_dt(value: str) -> str:
    text = non_empty_text(value)
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def _infer_started_at(summary: Dict[str, Any]) -> str:
    for key in ["started_at", "开始时间"]:
        value = _format_dt(str(summary.get(key, "")))
        if value:
            return value

    run_dir = non_empty_text(summary.get("run_dir") or summary.get("_run_dir"))
    if run_dir:
        match = re.search(r"(\d{8})_(\d{6})", run_dir)
        if match:
            try:
                return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                return ""
    return ""


def _infer_finished_at(summary: Dict[str, Any]) -> str:
    for key in ["finished_at", "结束时间"]:
        value = _format_dt(str(summary.get(key, "")))
        if value:
            return value

    summary_path = non_empty_text(summary.get("_summary_path"))
    if summary_path:
        path = Path(summary_path)
        if path.exists():
            return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return ""


def _run_time_text(summary: Dict[str, Any]) -> str:
    started_at = _infer_started_at(summary)
    finished_at = _infer_finished_at(summary)
    if started_at and finished_at:
        return f"{started_at} 至 {finished_at}"
    if started_at:
        return started_at
    if finished_at:
        return finished_at
    return "-"


def is_problem_correct(pass_count: int, sample_count: int) -> bool:
    """Return whether a problem is correct by the project majority rule."""

    return sample_count > 0 and pass_count / float(sample_count) >= 0.5


def is_valid_sample_status(item: Dict[str, Any]) -> bool:
    if item.get("error"):
        return False
    if item.get("valid_sample") is False:
        return False
    if item.get("judge_status_id") is not None or item.get("status_id") is not None:
        return True
    if item.get("judge_status_description") or item.get("status_description"):
        desc = non_empty_text(item.get("judge_status_description") or item.get("status_description"))
        return desc not in {"NOT_RUN", "ERROR"}
    if item.get("is_correct") is not None:
        return True
    return bool(item.get("judge_passed"))


def build_sample_status_summary(sample_statuses: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    statuses = list(sample_statuses)
    sample_count = len(statuses)
    valid_samples = sum(1 for item in statuses if is_valid_sample_status(item))
    passed_samples = sum(1 for item in statuses if is_valid_sample_status(item) and (item.get("judge_passed") or item.get("is_correct")))
    failed_samples = sample_count - passed_samples

    counter: Counter[tuple[Optional[int], str]] = Counter()
    for item in statuses:
        status_id = safe_int(item.get("status_id") or item.get("judge_status_id"), None)
        status_description = (
            non_empty_text(item.get("status_description"))
            or non_empty_text(item.get("judge_status_description"))
            or ("Accepted" if item.get("judge_passed") or item.get("is_correct") else "Wrong Answer")
        )
        counter[(status_id, status_description)] += 1

    distribution = [
        {
            "judge_status_id": status_id,
            "judge_status_description": status_description,
            "count": count,
        }
        for (status_id, status_description), count in counter.items()
    ]
    distribution.sort(key=lambda item: (-item["count"], item["judge_status_id"] or -1, item["judge_status_description"]))

    return {
        "sample_count": sample_count,
        "valid_samples": valid_samples,
        "passed_samples": passed_samples,
        "failed_samples": failed_samples,
        "invalid_samples": sample_count - valid_samples,
        "sample_pass_rate": _rate(passed_samples, sample_count),
        "status_distribution": distribution,
    }


def build_problem_level_summary(problems: List[Dict[str, Any]]) -> Dict[str, Any]:
    included = [item for item in problems if item.get("included_in_accuracy", True)]
    excluded = [item for item in problems if not item.get("included_in_accuracy", True)]
    total_problems = len(problems)
    denominator_problems = len(included)
    correct_problem_count = sum(1 for item in included if item.get("problem_correct"))
    wrong_problem_count = denominator_problems - correct_problem_count
    return {
        "judgment_rule": MAJORITY_RULE_DESCRIPTION,
        "total_problems": total_problems,
        "denominator_problems": denominator_problems,
        "excluded_problem_count": len(excluded),
        "excluded_problem_ids": [
            item.get("problem_id") or item.get("case_id") or item.get("index")
            for item in excluded
        ],
        "correct_problem_count": correct_problem_count,
        "wrong_problem_count": wrong_problem_count,
        "problem_accuracy": _rate(correct_problem_count, denominator_problems),
        "problem_wrong_rate": _rate(wrong_problem_count, denominator_problems),
    }


def build_chinese_run_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    problem_stats = summary.get("problem_status_summary") or {}
    sample_stats = summary.get("judge_status_summary") or summary.get("sample_status_summary") or {}
    problems = summary.get("problems") or []
    samples_per_problem = (
        summary.get("samples_per_problem")
        or summary.get("samples_per_question")
        or summary.get("每题采样次数")
    )

    return {
        "运行模式": summary.get("mode", ""),
        "模型": summary.get("model", ""),
        "开始时间": _infer_started_at(summary),
        "结束时间": _infer_finished_at(summary),
        "判定规则": problem_stats.get("judgment_rule") or summary.get("judgment_rule") or MAJORITY_RULE_DESCRIPTION,
        "配置": {
            "模型": summary.get("model", ""),
            "每题采样次数": samples_per_problem,
        },
        "题目统计": {
            "题目总数": problem_stats.get("total_problems", summary.get("total_problems", 0)),
            "计入正确率分母的题目数": problem_stats.get("denominator_problems", summary.get("total_problems", 0)),
            "未计入分母题目数": problem_stats.get("excluded_problem_count", 0),
            "未计入分母题目ID": problem_stats.get("excluded_problem_ids", []),
            "正确题数": problem_stats.get("correct_problem_count", 0),
            "错误题数": problem_stats.get("wrong_problem_count", 0),
            "题目正确率": problem_stats.get("problem_accuracy", 0.0),
            "题目错误率": problem_stats.get("problem_wrong_rate", 0.0),
        },
        "样本统计": {
            "总采样数": sample_stats.get("total_samples", sample_stats.get("sample_count", 0)),
            "有效采样数": sample_stats.get("valid_samples", sample_stats.get("total_samples", sample_stats.get("sample_count", 0))),
            "无效采样数": sample_stats.get("invalid_samples", 0),
            "做对样本数": sample_stats.get("passed_samples", 0),
            "做错样本数": sample_stats.get("failed_samples", 0),
            "样本正确率": sample_stats.get("sample_pass_rate", 0.0),
        },
        "题目明细": [
            {
                "题目ID": item.get("problem_id") or item.get("case_id") or item.get("index", ""),
                "题型": item.get("question_type", ""),
                "采样数": item.get("sample_count", item.get("samples", 0)),
                "有效采样数": item.get("valid_sample_count", item.get("sample_count", item.get("samples", 0))),
                "做对样本数": item.get("passed_samples", item.get("pass_count", item.get("correct_count", 0))),
                "做错样本数": item.get("failed_samples", 0),
                "样本正确率": item.get("sample_pass_rate", item.get("pass_rate", 0.0)),
                "是否计入分母": item.get("included_in_accuracy", True),
                "题目结果": "正确" if item.get("problem_correct") else ("未计入" if not item.get("included_in_accuracy", True) else "错误"),
                "样本明细": [
                    {
                        "sample": sample.get("sample_index") or sample.get("sample_no"),
                        "是否有效": sample.get("valid_sample", is_valid_sample_status(sample)),
                        "是否正确": bool(sample.get("judge_passed") or sample.get("is_correct")),
                        "状态": sample.get("judge_status_description") or sample.get("status_description") or "",
                    }
                    for sample in item.get("sample_statuses", [])
                ],
            }
            for item in problems
        ],
    }


def _find_pdf_browser() -> Optional[str]:
    candidates = [
        "msedge",
        "msedge.exe",
        "chrome",
        "chrome.exe",
        "chromium",
        "chromium.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
        path = Path(candidate)
        if path.exists():
            return str(path)
    return None


def _markdown_to_html(markdown_text: str) -> str:
    body: List[str] = []
    lines = markdown_text.splitlines()
    idx = 0
    in_ul = False

    def close_ul() -> None:
        nonlocal in_ul
        if in_ul:
            body.append("</ul>")
            in_ul = False

    while idx < len(lines):
        line = lines[idx].rstrip()
        stripped = line.strip()
        if not stripped:
            close_ul()
            idx += 1
            continue

        if stripped.startswith("|") and idx + 1 < len(lines) and set(lines[idx + 1].strip().replace("|", "").replace(":", "").replace("-", "")) <= {""}:
            close_ul()
            headers = [cell.strip() for cell in stripped.strip("|").split("|")]
            aligns = [cell.strip() for cell in lines[idx + 1].strip().strip("|").split("|")]
            idx += 2
            rows: List[List[str]] = []
            while idx < len(lines) and lines[idx].strip().startswith("|"):
                rows.append([cell.strip() for cell in lines[idx].strip().strip("|").split("|")])
                idx += 1
            body.append("<table>")
            body.append("<thead><tr>" + "".join(f"<th>{_inline_md(cell)}</th>" for cell in headers) + "</tr></thead>")
            body.append("<tbody>")
            for row in rows:
                body.append("<tr>" + "".join(f"<td>{_inline_md(cell)}</td>" for cell in row) + "</tr>")
            body.append("</tbody></table>")
            continue

        if stripped.startswith("# "):
            close_ul()
            body.append(f"<h1>{_inline_md(stripped[2:].strip())}</h1>")
        elif stripped.startswith("## "):
            close_ul()
            body.append(f"<h2>{_inline_md(stripped[3:].strip())}</h2>")
        elif stripped.startswith("### "):
            close_ul()
            body.append(f"<h3>{_inline_md(stripped[4:].strip())}</h3>")
        elif stripped.startswith("- "):
            if not in_ul:
                body.append("<ul>")
                in_ul = True
            body.append(f"<li>{_inline_md(stripped[2:].strip())}</li>")
        else:
            close_ul()
            body.append(f"<p>{_inline_md(stripped)}</p>")
        idx += 1

    close_ul()
    return "\n".join(body)


def _inline_md(text: str) -> str:
    escaped = html.escape(text)
    parts = escaped.split("`")
    for i in range(1, len(parts), 2):
        parts[i] = f"<code>{parts[i]}</code>"
    return "".join(parts)


def write_markdown_pdf(markdown_path: Path, pdf_path: Optional[Path] = None) -> bool:
    """Render Markdown to PDF through a local Chromium-family browser if available."""

    browser = _find_pdf_browser()
    if not browser:
        return False

    pdf_path = pdf_path or markdown_path.with_suffix(".pdf")
    markdown_text = markdown_path.read_text(encoding="utf-8")
    html_body = _markdown_to_html(markdown_text)
    html_text = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{
      font-family: "Microsoft YaHei", "SimSun", Arial, sans-serif;
      color: #111827;
      margin: 28px;
      font-size: 13px;
      line-height: 1.45;
    }}
    h1 {{ font-size: 24px; margin: 0 0 18px; }}
    h2 {{ font-size: 18px; margin: 22px 0 10px; border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 8px 0 16px; table-layout: auto; }}
    th, td {{ border: 1px solid #d1d5db; padding: 6px 8px; vertical-align: top; word-break: break-word; }}
    th {{ background: #f3f4f6; font-weight: 600; }}
    code {{ font-family: Consolas, "Microsoft YaHei", monospace; background: #f3f4f6; padding: 1px 3px; border-radius: 3px; }}
    ul {{ margin: 6px 0 14px 20px; padding: 0; }}
  </style>
</head>
<body>
{html_body}
</body>
</html>
"""

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / f"{markdown_path.stem}.html"
        html_path.write_text(html_text, encoding="utf-8")
        command = [
            browser,
            "--headless",
            "--disable-gpu",
            "--no-first-run",
            "--no-pdf-header-footer",
            "--print-to-pdf-no-header",
            f"--print-to-pdf={pdf_path.resolve()}",
            html_path.resolve().as_uri(),
        ]
        try:
            completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        except Exception:
            return False
    return completed.returncode == 0 and pdf_path.exists()


def write_summary_markdown(summary: Dict[str, Any], path: Path) -> None:
    """Write a concise Markdown report for code or math runs."""

    mode = non_empty_text(summary.get("mode")) or "run"
    label = _mode_label(mode)
    problem_stats = summary.get("problem_status_summary") or {}
    problems = summary.get("problems") or []
    sample_stats = summary.get("judge_status_summary") or summary.get("sample_status_summary") or {}

    excluded_ids = problem_stats.get("excluded_problem_ids", [])
    total = int(problem_stats.get("total_problems", summary.get("total_problems", 0)) or 0)
    denominator = int(problem_stats.get("denominator_problems", total) or 0)
    excluded = int(problem_stats.get("excluded_problem_count", total - denominator) or 0)
    correct = int(problem_stats.get("correct_problem_count", 0) or 0)
    wrong = int(problem_stats.get("wrong_problem_count", 0) or 0)
    accuracy = float(problem_stats.get("problem_accuracy", 0.0))
    wrong_rate = float(problem_stats.get("problem_wrong_rate", 0.0))
    samples_per_problem = _samples_per_problem(summary)

    lines = [
        "# 运行评测报告",
        "",
        "## 配置",
        "",
        "| 类别 | 使用模型 | 每道题采样次数 | 运行时间 |",
        "|---|---|---:|---|",
        f"| {label} | `{summary.get('model', '')}` | {samples_per_problem} | {_run_time_text(summary)} |",
        "",
        "## 总览",
        "",
        "| 类别 | 总题目数 | 计入题目数 | 不计入数量 | 做对数量 | 做错数量 | 做对率 | 做错率 | 使用模型 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        f"| {label} | {total} | {denominator} | {excluded} | {correct} | {wrong} | {_pct(accuracy)} | {_pct(wrong_rate)} | `{summary.get('model', '')}` |",
        "",
        "## 样本统计",
        "",
        "| 类别 | 总采样数 | 有效采样数 | 无效采样数 | 做对样本 | 做错样本 | 样本做对率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| {label} | {sample_stats.get('total_samples', sample_stats.get('sample_count', 0))} | "
        f"{sample_stats.get('valid_samples', sample_stats.get('total_samples', sample_stats.get('sample_count', 0)))} | "
        f"{sample_stats.get('invalid_samples', 0)} | {sample_stats.get('passed_samples', 0)} | "
        f"{sample_stats.get('failed_samples', 0)} | {_pct(float(sample_stats.get('sample_pass_rate', 0.0)))} |",
        "",
        "## 题目明细",
        "",
        "| 类别 | 题目ID | 采样数 | 有效采样数 | 做对样本 | 做错样本 | 样本做对率 | 题目结果 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in problems:
        sample_count = int(item.get("sample_count", item.get("samples", 0)) or 0)
        valid_count = int(item.get("valid_sample_count", sample_count) or 0)
        passed = int(item.get("passed_samples", item.get("pass_count", item.get("correct_count", 0))) or 0)
        failed = int(item.get("failed_samples", sample_count - passed) or 0)
        pass_rate = float(item.get("sample_pass_rate", item.get("pass_rate", _rate(passed, sample_count))) or 0.0)
        included = bool(item.get("included_in_accuracy", True))
        result = "正确" if item.get("problem_correct") else ("未计入" if not included else "错误")
        problem_id = item.get("problem_id") or item.get("case_id") or item.get("index", "")
        lines.append(f"| {label} | `{problem_id}` | {sample_count} | {valid_count} | {passed} | {failed} | {_pct(pass_rate)} | {result} |")

    lines.extend([
        "",
        "## 结论",
        "",
        f"- 本次{label}共运行 {total} 题，计入正确率分母 {denominator} 题，做对 {correct} 题，做错 {wrong} 题，做对率 {_pct(accuracy)}。",
    ])
    if excluded_ids:
        lines.append(f"- 有 {len(excluded_ids)} 道题没有有效采样结果，未计入题目正确率分母：{', '.join(f'`{x}`' for x in excluded_ids)}。")
    lines.append("")
    write_text(path, "\n".join(lines))


def write_run_outputs(run_dir: Path, summary: Dict[str, Any]) -> None:
    enriched_summary = dict(summary)
    enriched_summary.setdefault("run_dir", str(run_dir))
    enriched_summary["_summary_path"] = str(run_dir / "run_summary.json")
    enriched_summary["_run_dir"] = str(run_dir)
    write_json(run_dir / "run_summary.json", summary)
    write_json(run_dir / "summary.json", build_chinese_run_summary(enriched_summary))
    write_summary_markdown(enriched_summary, run_dir / "summary.md")
    write_markdown_pdf(run_dir / "summary.md")


def load_summary(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def find_latest_summary(root: Path) -> Optional[Dict[str, Any]]:
    candidates = sorted(root.glob("*/run_summary.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for candidate in candidates:
        data = load_summary(candidate)
        if data:
            data["_summary_path"] = str(candidate)
            data["_run_dir"] = str(candidate.parent)
            return data
    return None


def write_combined_latest_report(runs_root: Path = Path("runs"), reports_dir: Path = Path("reports")) -> None:
    code_summary = find_latest_summary(runs_root / "code")
    math_summary = find_latest_summary(runs_root / "math")
    summaries = [item for item in [code_summary, math_summary] if item]

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y-%m-%d_%H%M%S")
    lines = [
        "# 最新综合评测报告",
        "",
        f"- 生成日期：{today}",
        "",
    ]
    if not summaries:
        lines.append("当前没有可用的运行结果。")
        content = "\n".join(lines)
        write_text(reports_dir / "summary.md", content)
        write_text(reports_dir / f"summary_{timestamp}.md", content)
        return

    total_problems = 0
    total_denominator = 0
    total_correct = 0
    total_wrong = 0
    total_excluded = 0
    total_samples = 0
    total_valid_samples = 0
    total_passed_samples = 0
    total_failed_samples = 0

    for summary in summaries:
        ps = summary.get("problem_status_summary") or {}
        ss = summary.get("judge_status_summary") or {}
        total_problems += int(ps.get("total_problems", summary.get("total_problems", 0)) or 0)
        total_denominator += int(ps.get("denominator_problems", ps.get("total_problems", 0)) or 0)
        total_correct += int(ps.get("correct_problem_count", 0) or 0)
        total_wrong += int(ps.get("wrong_problem_count", 0) or 0)
        total_excluded += int(ps.get("excluded_problem_count", 0) or 0)
        total_samples += int(ss.get("total_samples", ss.get("sample_count", 0)) or 0)
        total_valid_samples += int(ss.get("valid_samples", ss.get("total_samples", ss.get("sample_count", 0))) or 0)
        total_passed_samples += int(ss.get("passed_samples", 0) or 0)
        total_failed_samples += int(ss.get("failed_samples", 0) or 0)

    lines.extend([
        "## 配置",
        "",
        "| 类别 | 使用模型 | 每道题采样次数 | 运行目录 |",
        "|---|---|---:|---|",
    ])
    for summary in summaries:
        label = _mode_label(summary.get("mode", ""))
        lines.append(
            f"| {label} | `{summary.get('model', '')}` | {_samples_per_problem(summary)} | "
            f"`{summary.get('_run_dir', summary.get('run_dir', ''))}` |"
        )
    lines.extend([
        "",
        "## 总览",
        "",
        "| 类别 | 总题目数 | 计入题目数 | 不计入数量 | 做对数量 | 做错数量 | 做对率 | 做错率 | 使用模型 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    models: List[str] = []
    for summary in summaries:
        ps = summary.get("problem_status_summary") or {}
        mode = summary.get("mode", "")
        label = _mode_label(mode)
        model = non_empty_text(summary.get("model"))
        if model and model not in models:
            models.append(model)
        denominator = int(ps.get("denominator_problems", ps.get("total_problems", 0)) or 0)
        lines.append(
            f"| {label} | {ps.get('total_problems', summary.get('total_problems', 0))} | {denominator} | "
            f"{ps.get('excluded_problem_count', 0)} | {ps.get('correct_problem_count', 0)} | "
            f"{ps.get('wrong_problem_count', 0)} | {_pct(float(ps.get('problem_accuracy', 0.0) or 0.0))} | "
            f"{_pct(float(ps.get('problem_wrong_rate', 0.0) or 0.0))} | `{model}` |"
        )
    lines.append(
        f"| 合计 | {total_problems} | {total_denominator} | {total_excluded} | {total_correct} | {total_wrong} | "
        f"{_pct(_rate(total_correct, total_denominator))} | {_pct(_rate(total_wrong, total_denominator))} | "
        f"`{', '.join(models)}` |"
    )

    lines.extend([
        "",
        "## 样本统计",
        "",
        f"- 总采样数：{total_samples}",
        f"- 有效采样数：{total_valid_samples}",
        f"- 做对样本：{total_passed_samples}",
        f"- 做错样本：{total_failed_samples}",
        f"- 样本正确率：{_pct(_rate(total_passed_samples, total_samples))}",
        "",
        "## 题目明细",
        "",
        "| 类别 | 题目ID | 采样数 | 有效采样数 | 做对样本 | 做错样本 | 样本做对率 | 题目结果 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ])
    for summary in summaries:
        label = _mode_label(summary.get("mode", ""))
        for item in summary.get("problems", []):
            sample_count = int(item.get("sample_count", item.get("samples", 0)) or 0)
            valid_count = int(item.get("valid_sample_count", sample_count) or 0)
            passed = int(item.get("passed_samples", item.get("pass_count", item.get("correct_count", 0))) or 0)
            failed = int(item.get("failed_samples", sample_count - passed) or 0)
            pass_rate = float(item.get("sample_pass_rate", item.get("pass_rate", _rate(passed, sample_count))) or 0.0)
            included = bool(item.get("included_in_accuracy", True))
            result = "正确" if item.get("problem_correct") else ("未计入" if not included else "错误")
            problem_id = item.get("problem_id") or item.get("case_id") or item.get("index", "")
            lines.append(f"| {label} | `{problem_id}` | {sample_count} | {valid_count} | {passed} | {failed} | {_pct(pass_rate)} | {result} |")

    lines.extend([
        "",
        "## 结论",
        "",
        f"- 最新代码题和数学题合计共运行 {total_problems} 题，计入分母 {total_denominator} 题，做对 {total_correct} 题，做错 {total_wrong} 题，做对率 {_pct(_rate(total_correct, total_denominator))}。",
        f"- 未计入分母题目数：{total_excluded}。",
        "",
    ])
    content = "\n".join(lines)
    write_text(reports_dir / "summary.md", content)
    write_text(reports_dir / f"summary_{timestamp}.md", content)
    write_markdown_pdf(reports_dir / "summary.md")
    write_markdown_pdf(reports_dir / f"summary_{timestamp}.md")
