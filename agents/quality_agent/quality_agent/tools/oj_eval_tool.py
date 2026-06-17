"""oj-eval 的 LangChain Tool 适配器。

Agent 不直接重写评测逻辑，而是把已有 `oj-eval` CLI 封装成一个
LangChain StructuredTool。这样 LangGraph 节点只负责流程编排，工具层
负责真实执行。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List

from ..core.config import ModelConfig
from ..standardization.normalizer import load_json_items

ProgressCallback = Callable[[Dict[str, Any]], None]


def _emit_tool_event(
    callback: ProgressCallback | None,
    *,
    status: str,
    message: str,
) -> None:
    """向 UI 回传 oj-eval 子进程运行状态。"""

    if callback is None:
        return
    callback(
        {
            "time": datetime.now().strftime("%H:%M:%S"),
            "step": "run_eval",
            "label": "评测工具",
            "status": status,
            "message": message,
        }
    )


def _redact_args(args: List[str]) -> List[str]:
    """脱敏命令行参数，避免 API Key 出现在报告或日志中。"""

    redacted: List[str] = []
    hide_next = False
    for value in args:
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        redacted.append(value)
        if value in {"--ppio-api-key"}:
            hide_next = True
    return redacted


def _parse_stdout_json(stdout: str) -> Dict[str, Any]:
    """从 oj-eval 标准输出中提取 JSON 摘要。"""

    text = stdout.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _langfuse_trace_diagnostic(*, enabled: bool, stderr: str) -> Dict[str, Any]:
    """把子进程里的 Langfuse SDK 输出转成不含密钥的诊断信息。"""

    if not enabled:
        return {"enabled": False, "status": "disabled", "message": "未启用 Langfuse 轨迹记录。"}

    text = stderr or ""
    if "No Langfuse client" in text:
        return {
            "enabled": True,
            "status": "client_not_initialized",
            "message": "Langfuse 客户端没有正确初始化，请确认已使用最新代码并重启页面。",
        }
    if "Invalid credentials" in text or "Unauthorized" in text or "code: 401" in text or "status_code: 401" in text:
        return {
            "enabled": True,
            "status": "auth_failed",
            "message": "Langfuse 认证失败：Public Key、Secret Key 或 Base URL 不匹配。",
        }
    if "Failed to export span batch" in text:
        return {
            "enabled": True,
            "status": "export_failed",
            "message": "Langfuse 轨迹上传失败，请检查网络、Base URL 和服务端状态。",
        }
    return {
        "enabled": True,
        "status": "no_local_error",
        "message": "本地未发现 Langfuse 上传错误；如果页面仍无 trace，请先使用“检测 Langfuse 配置”。",
    }


def _merge_langfuse_trace_diagnostics(diagnostics: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """合并 code/math 子任务的 Langfuse 诊断。"""

    if not diagnostics:
        return {}
    failed = {
        key: value
        for key, value in diagnostics.items()
        if value.get("status") not in {"disabled", "no_local_error"}
    }
    if failed:
        message = "；".join(f"{key}: {value.get('message', '')}" for key, value in failed.items())
        return {
            "enabled": True,
            "status": "has_errors",
            "message": message,
            "domains": diagnostics,
        }
    return {
        "enabled": True,
        "status": "no_local_error",
        "message": "各子任务本地均未发现 Langfuse 上传错误。",
        "domains": diagnostics,
    }


def _model_args(
    *,
    api_key: str = "",
    base_url: str = "",
    model_name: str = "",
) -> List[str]:
    """把模型配置转换成 oj-eval CLI 参数。"""

    args: List[str] = []
    if api_key:
        args.extend(["--ppio-api-key", api_key])
    if base_url:
        args.extend(["--ppio-base-url", base_url])
    if model_name:
        args.extend(["--model", model_name])
    return args


def _tool_subprocess_env(
    *,
    enable_langfuse: bool = False,
    langfuse_public_key: str = "",
    langfuse_secret_key: str = "",
    langfuse_base_url: str = "",
    quality_agent_run_id: str = "",
    quality_agent_run_dir: str = "",
    dataset_path: str = "",
    dataset_type: str = "",
) -> Dict[str, str]:
    """构造调用 oj-eval 工具时的子进程环境。"""

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if enable_langfuse:
        env["QUALITY_AGENT_LANGFUSE_ENABLED"] = "1"
        env["QUALITY_AGENT_RUN_ID"] = quality_agent_run_id
        env["QUALITY_AGENT_RUN_DIR"] = quality_agent_run_dir
        env["QUALITY_AGENT_DATASET_PATH"] = dataset_path
        env["QUALITY_AGENT_DATASET_TYPE"] = dataset_type
        if langfuse_public_key:
            env["LANGFUSE_PUBLIC_KEY"] = langfuse_public_key
        if langfuse_secret_key:
            env["LANGFUSE_SECRET_KEY"] = langfuse_secret_key
        if langfuse_base_url:
            env["LANGFUSE_BASE_URL"] = langfuse_base_url
            env["LANGFUSE_HOST"] = langfuse_base_url
    source_root = Path(__file__).resolve().parents[4] / "tools" / "oj_eval"
    if source_root.exists():
        current = env.get("PYTHONPATH", "")
        parts = [str(source_root)]
        if current:
            parts.append(current)
        env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _enrich_result_from_run_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """从 oj-eval 写入的 UTF-8 run_summary.json 补齐 stdout 摘要缺失的明细。"""

    run_dir_text = str(result.get("run_dir") or "").strip()
    if not run_dir_text:
        return result

    summary_path = Path(run_dir_text) / "run_summary.json"
    if not summary_path.exists():
        return result

    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        result["summary_load_error"] = f"Could not read run_summary.json: {exc}"
        return result

    preserved = {
        "tool": result.get("tool"),
        "command": result.get("command"),
        "returncode": result.get("returncode"),
        "stdout": result.get("stdout"),
        "stderr": result.get("stderr"),
        "tool_invocation": result.get("tool_invocation"),
    }
    result.update(summary)
    for key, value in preserved.items():
        if value is not None:
            result[key] = value
    result["tool"] = result.get("tool") or "oj_eval"
    return result


def _dry_run_summary(dataset_path: Path, dataset_type: str) -> Dict[str, Any]:
    """生成不触发模型调用的工具摘要。"""

    total_problems = 0
    try:
        total_problems = len(_load_case_items(dataset_path))
    except Exception:
        total_problems = 0
    return {
        "mode": dataset_type,
        "dry_run": True,
        "cases_file": str(dataset_path),
        "run_dir": "",
        "total_problems": total_problems,
        "problem_status_summary": {},
        "judge_status_summary": {},
        "problems": [],
    }


def _load_case_items(dataset_path: Path) -> List[Dict[str, Any]]:
    """读取题库 JSON/JSONL 并返回题目列表。"""

    items, _ = load_json_items(dataset_path)
    return items


def _detect_case_type(item: Dict[str, Any]) -> str:
    """按字段识别混合题库里的单题类型。"""

    standard_type = str(item.get("type") or "").strip().lower()
    if standard_type in {"programming", "code", "oj"} or str(item.get("problem_text") or "").strip():
        return "code"
    if (
        standard_type in {"single_choice", "multiple_choice", "judge_choice"}
        or str(item.get("question") or item.get("answer") or item.get("gtupe") or item.get("question_type") or "").strip()
    ):
        return "math"
    return "unknown"


def _write_split_cases(path: Path, items: List[Dict[str, Any]]) -> None:
    """写入拆分后的临时题库。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cases": items}, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_judge_status_summaries(summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """合并 code/math 两路评测的 sample 级统计。"""

    total_samples = sum(int(item.get("total_samples", 0) or item.get("sample_count", 0) or 0) for item in summaries)
    valid_samples = sum(int(item.get("valid_samples", 0) or 0) for item in summaries)
    invalid_samples = sum(int(item.get("invalid_samples", 0) or 0) for item in summaries)
    passed_samples = sum(int(item.get("passed_samples", 0) or 0) for item in summaries)
    failed_samples = sum(int(item.get("failed_samples", 0) or 0) for item in summaries)

    distribution: Dict[tuple[Any, str], int] = {}
    for summary in summaries:
        for row in summary.get("status_distribution", []) or []:
            status_id = row.get("judge_status_id", row.get("status_id"))
            status_desc = str(row.get("judge_status_description") or row.get("status_description") or "")
            key = (status_id, status_desc)
            distribution[key] = distribution.get(key, 0) + int(row.get("count", 0) or 0)

    def sort_status_id(value: Any) -> int:
        try:
            return -1 if value is None else int(value)
        except Exception:
            return -1

    return {
        "total_samples": total_samples,
        "valid_samples": valid_samples,
        "invalid_samples": invalid_samples,
        "passed_samples": passed_samples,
        "failed_samples": failed_samples,
        "sample_pass_rate": (passed_samples / float(total_samples)) if total_samples else 0.0,
        "status_distribution": [
            {
                "judge_status_id": status_id,
                "judge_status_description": status_desc,
                "count": count,
            }
            for (status_id, status_desc), count in sorted(
                distribution.items(),
                key=lambda item: (-item[1], sort_status_id(item[0][0]), item[0][1]),
            )
        ],
    }


def _build_problem_status_summary(problems: List[Dict[str, Any]]) -> Dict[str, Any]:
    """根据合并后的 problems 生成题目级统计。"""

    total = len(problems)
    correct = sum(1 for item in problems if item.get("problem_correct"))
    wrong = total - correct
    return {
        "judgment_rule": "majority_vote",
        "total_problems": total,
        "correct_problem_count": correct,
        "wrong_problem_count": wrong,
        "problem_accuracy": (correct / float(total)) if total else 0.0,
        "problem_wrong_rate": (wrong / float(total)) if total else 0.0,
    }


def _merge_mixed_results(
    *,
    dataset: Path,
    output: Path,
    code_items: List[Dict[str, Any]],
    math_items: List[Dict[str, Any]],
    unknown_items: int,
    code_result: Dict[str, Any] | None,
    math_result: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """合并混合题库拆分后的两路 oj-eval 结果。"""

    sub_results = {key: value for key, value in {"code": code_result, "math": math_result}.items() if value}
    problems: List[Dict[str, Any]] = []
    judge_summaries: List[Dict[str, Any]] = []
    errors: List[str] = []
    commands: Dict[str, Any] = {}
    langfuse_diagnostics: Dict[str, Dict[str, Any]] = {}

    for key, result in sub_results.items():
        problems.extend(result.get("problems", []) or [])
        if result.get("judge_status_summary"):
            judge_summaries.append(result.get("judge_status_summary", {}))
        if result.get("error"):
            errors.append(f"{key}: {result['error']}")
        if result.get("command"):
            commands[key] = result.get("command")
        if result.get("langfuse_trace"):
            langfuse_diagnostics[key] = result.get("langfuse_trace", {})

    problem_status_summary = _build_problem_status_summary(problems)
    judge_status_summary = _merge_judge_status_summaries(judge_summaries)
    combined: Dict[str, Any] = {
        "mode": "mixed",
        "tool": "oj_eval",
        "cases_file": str(dataset),
        "run_dir": str(output),
        "dry_run": False,
        "returncode": 1 if errors else 0,
        "total_problems": len(problems),
        "problem_status_summary": problem_status_summary,
        "judge_status_summary": judge_status_summary,
        "problems": problems,
        "commands": commands,
        "tool_invocation": {
            "framework": "mixed_split",
            "code_items": len(code_items),
            "math_items": len(math_items),
            "unknown_items": unknown_items,
        },
        "sub_results": {
            key: {
                "mode": value.get("mode"),
                "returncode": value.get("returncode"),
                "total_problems": value.get("total_problems"),
                "run_dir": value.get("run_dir"),
                "error": value.get("error", ""),
                "langfuse_trace": value.get("langfuse_trace", {}),
            }
            for key, value in sub_results.items()
        },
    }
    if langfuse_diagnostics:
        combined["langfuse_trace"] = _merge_langfuse_trace_diagnostics(langfuse_diagnostics)
    if errors:
        combined["error"] = "; ".join(errors)
    return combined


def run_oj_eval_cli(
    *,
    dataset_path: str,
    dataset_type: str,
    samples: int,
    output_dir: str,
    dry_run: bool,
    solver_base_url: str = "",
    solver_api_key: str = "",
    solver_model_name: str = "",
    enable_langfuse: bool = False,
    langfuse_public_key: str = "",
    langfuse_secret_key: str = "",
    langfuse_base_url: str = "",
    quality_agent_run_id: str = "",
    quality_agent_run_dir: str = "",
    progress_callback: ProgressCallback | None = None,
) -> Dict[str, Any]:
    """执行 oj-eval CLI，并返回结构化摘要。"""

    dataset = Path(dataset_path)
    output = Path(output_dir)
    if dry_run:
        _emit_tool_event(progress_callback, status="running", message="Dry run 已开启，跳过 oj-eval 子进程调用。")
        result = _dry_run_summary(dataset, dataset_type)
        result["tool"] = "oj_eval"
        result["tool_invocation"] = {
            "framework": "direct",
            "mode": "dry_run",
        }
        _emit_tool_event(progress_callback, status="completed", message="Dry run 工具摘要已生成。")
        return result

    output.mkdir(parents=True, exist_ok=True)
    base_cmd = [sys.executable, "-m", "oj_eval"]
    model_args = _model_args(
        api_key=solver_api_key,
        base_url=solver_base_url,
        model_name=solver_model_name,
    )

    if dataset_type == "mixed":
        items = _load_case_items(dataset)
        code_items = [item for item in items if _detect_case_type(item) == "code"]
        math_items = [item for item in items if _detect_case_type(item) == "math"]
        unknown_items = len(items) - len(code_items) - len(math_items)
        split_dir = output / "split_cases"
        code_result: Dict[str, Any] | None = None
        math_result: Dict[str, Any] | None = None
        _emit_tool_event(
            progress_callback,
            status="running",
            message=f"混合题库已拆分：代码题 {len(code_items)}，数学题 {len(math_items)}，未知 {unknown_items}。",
        )
        if code_items:
            code_cases = split_dir / "code_cases.json"
            _write_split_cases(code_cases, code_items)
            code_result = run_oj_eval_cli(
                dataset_path=str(code_cases),
                dataset_type="code",
                samples=samples,
                output_dir=str(output / "code"),
                dry_run=False,
                solver_base_url=solver_base_url,
                solver_api_key=solver_api_key,
                solver_model_name=solver_model_name,
                enable_langfuse=enable_langfuse,
                langfuse_public_key=langfuse_public_key,
                langfuse_secret_key=langfuse_secret_key,
                langfuse_base_url=langfuse_base_url,
                quality_agent_run_id=quality_agent_run_id,
                quality_agent_run_dir=quality_agent_run_dir,
                progress_callback=progress_callback,
            )
        if math_items:
            math_cases = split_dir / "math_cases.json"
            _write_split_cases(math_cases, math_items)
            math_result = run_oj_eval_cli(
                dataset_path=str(math_cases),
                dataset_type="math",
                samples=samples,
                output_dir=str(output / "math"),
                dry_run=False,
                solver_base_url=solver_base_url,
                solver_api_key=solver_api_key,
                solver_model_name=solver_model_name,
                enable_langfuse=enable_langfuse,
                langfuse_public_key=langfuse_public_key,
                langfuse_secret_key=langfuse_secret_key,
                langfuse_base_url=langfuse_base_url,
                quality_agent_run_id=quality_agent_run_id,
                quality_agent_run_dir=quality_agent_run_dir,
                progress_callback=progress_callback,
            )
        result = _merge_mixed_results(
            dataset=dataset,
            output=output,
            code_items=code_items,
            math_items=math_items,
            unknown_items=unknown_items,
            code_result=code_result,
            math_result=math_result,
        )
        _emit_tool_event(progress_callback, status="completed", message=f"混合题库评测合并完成，题目结果 {len(result.get('problems', []))} 条。")
        return result

    if dataset_type == "math":
        cmd = [
            *base_cmd,
            "--math",
            "--math-cases-file",
            str(dataset),
            "--math-samples",
            str(samples),
            "--math-quiz-output-dir",
            str(output / "math"),
            *model_args,
        ]
    elif dataset_type == "code":
        cmd = [
            *base_cmd,
            "--domain",
            "code",
            "--cases-file",
            str(dataset),
            "--samples",
            str(samples),
            "--output-dir",
            str(output / "code"),
            *model_args,
        ]
    else:
        return {
            "mode": dataset_type,
            "tool": "oj_eval",
            "error": f"Evaluation routing for dataset_type={dataset_type!r} is not implemented yet.",
            "problems": [],
        }

    redacted_cmd = _redact_args(cmd)
    _emit_tool_event(progress_callback, status="running", message=f"已启动 oj-eval：{' '.join(redacted_cmd)}")
    started = time.monotonic()
    stdout_path = output / f"oj_eval_{dataset_type}_stdout.log"
    stderr_path = output / f"oj_eval_{dataset_type}_stderr.log"
    with stdout_path.open("w", encoding="utf-8", errors="replace") as stdout_file, stderr_path.open(
        "w",
        encoding="utf-8",
        errors="replace",
    ) as stderr_file:
        process = subprocess.Popen(
            cmd,
            cwd=Path.cwd(),
            env=_tool_subprocess_env(
                enable_langfuse=enable_langfuse,
                langfuse_public_key=langfuse_public_key,
                langfuse_secret_key=langfuse_secret_key,
                langfuse_base_url=langfuse_base_url,
                quality_agent_run_id=quality_agent_run_id,
                quality_agent_run_dir=quality_agent_run_dir,
                dataset_path=str(dataset),
                dataset_type=dataset_type,
            ),
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
        )
        last_heartbeat = -1
        while process.poll() is None:
            elapsed = int(time.monotonic() - started)
            if elapsed // 2 != last_heartbeat:
                last_heartbeat = elapsed // 2
                _emit_tool_event(progress_callback, status="running", message=f"oj-eval 仍在运行，已耗时 {elapsed} 秒。")
            time.sleep(0.5)

    elapsed = int(time.monotonic() - started)
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    result: Dict[str, Any] = {
        "tool": "oj_eval",
        "command": redacted_cmd,
        "returncode": process.returncode,
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
    }
    if enable_langfuse:
        result["langfuse_trace"] = _langfuse_trace_diagnostic(enabled=True, stderr=stderr)
    if process.returncode != 0:
        result["error"] = f"oj-eval exited with code {process.returncode}"
        _emit_tool_event(progress_callback, status="failed", message=f"oj-eval 运行失败，退出码 {process.returncode}，耗时 {elapsed} 秒。")
        return result

    try:
        result.update(_parse_stdout_json(stdout))
        result = _enrich_result_from_run_summary(result)
        _emit_tool_event(progress_callback, status="completed", message=f"oj-eval 完成，耗时 {elapsed} 秒。")
    except Exception as exc:
        result["error"] = f"Could not parse oj-eval stdout JSON: {exc}"
        result = _enrich_result_from_run_summary(result)
        _emit_tool_event(progress_callback, status="failed", message=f"oj-eval 输出解析失败：{exc}")
    return result


def _build_args_schema() -> Any:
    """延迟构造 LangChain StructuredTool 参数模型。"""

    from pydantic import BaseModel, Field

    class OJEvalToolInput(BaseModel):
        """oj-eval 工具输入结构。"""

        dataset_path: str = Field(..., description="Path to the dataset JSON file.")
        dataset_type: str = Field(..., description="Dataset type: math, code, or mixed.")
        samples: int = Field(..., ge=1, description="Number of solver samples per item.")
        output_dir: str = Field(..., description="Directory for solver tool outputs.")
        dry_run: bool = Field(False, description="Whether to skip model calls.")
        solver_base_url: str = Field("", description="OpenAI-compatible solver base URL.")
        solver_api_key: str = Field("", description="Solver API key.")
        solver_model_name: str = Field("", description="Solver model name.")
        enable_langfuse: bool = Field(False, description="Whether to trace solver model calls to Langfuse.")
        langfuse_public_key: str = Field("", description="Langfuse public key.")
        langfuse_secret_key: str = Field("", description="Langfuse secret key.")
        langfuse_base_url: str = Field("", description="Langfuse base URL.")
        quality_agent_run_id: str = Field("", description="Quality-agent run id for trace metadata.")
        quality_agent_run_dir: str = Field("", description="Quality-agent run directory for trace metadata.")

    return OJEvalToolInput


def build_oj_eval_structured_tool() -> Any:
    """构建 LangChain StructuredTool 形式的 oj-eval 工具。"""

    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(
        name="oj_eval_tool",
        description=(
            "Run the existing oj-eval evaluator for math objective questions or "
            "code/OJ questions, then return a structured evaluation summary."
        ),
        func=run_oj_eval_cli,
        args_schema=_build_args_schema(),
    )


def _tool_payload(
    *,
    dataset_path: Path,
    dataset_type: str,
    samples: int,
    output_dir: Path,
    solver_model: ModelConfig | None,
    dry_run: bool,
    enable_langfuse: bool = False,
    langfuse_public_key: str = "",
    langfuse_secret_key: str = "",
    langfuse_base_url: str = "",
    quality_agent_run_id: str = "",
    quality_agent_run_dir: str = "",
) -> Dict[str, Any]:
    """把 Agent 内部配置转换成 LangChain Tool 输入。"""

    model = solver_model or ModelConfig()
    return {
        "dataset_path": str(dataset_path),
        "dataset_type": dataset_type,
        "samples": max(1, int(samples)),
        "output_dir": str(output_dir),
        "dry_run": bool(dry_run),
        "solver_base_url": model.base_url,
        "solver_api_key": model.api_key,
        "solver_model_name": model.model_name,
        "enable_langfuse": bool(enable_langfuse),
        "langfuse_public_key": langfuse_public_key,
        "langfuse_secret_key": langfuse_secret_key,
        "langfuse_base_url": langfuse_base_url,
        "quality_agent_run_id": quality_agent_run_id,
        "quality_agent_run_dir": quality_agent_run_dir,
    }


def invoke_oj_eval_tool(
    *,
    dataset_path: Path,
    dataset_type: str,
    samples: int,
    output_dir: Path,
    solver_model: ModelConfig | None,
    dry_run: bool,
    enable_langfuse: bool = False,
    langfuse_public_key: str = "",
    langfuse_secret_key: str = "",
    langfuse_base_url: str = "",
    quality_agent_run_id: str = "",
    quality_agent_run_dir: str = "",
    on_event: ProgressCallback | None = None,
) -> Dict[str, Any]:
    """通过 LangChain Tool 调用 oj-eval，缺少依赖时退回直接调用。"""

    payload = _tool_payload(
        dataset_path=dataset_path,
        dataset_type=dataset_type,
        samples=samples,
        output_dir=output_dir,
        solver_model=solver_model,
        dry_run=dry_run,
        enable_langfuse=enable_langfuse,
        langfuse_public_key=langfuse_public_key,
        langfuse_secret_key=langfuse_secret_key,
        langfuse_base_url=langfuse_base_url,
        quality_agent_run_id=quality_agent_run_id,
        quality_agent_run_dir=quality_agent_run_dir,
    )
    if on_event is not None:
        result = run_oj_eval_cli(**payload, progress_callback=on_event)
        invocation = dict(result.get("tool_invocation") or {})
        invocation.update(
            {
                "framework": "direct_progress",
                "tool_name": "oj_eval_cli",
            }
        )
        result["tool_invocation"] = invocation
        return result

    try:
        tool = build_oj_eval_structured_tool()
    except Exception as exc:
        result = run_oj_eval_cli(**payload)
        result["tool_invocation"] = {
            "framework": "direct_fallback",
            "fallback_reason": str(exc),
        }
        return result

    result = tool.invoke(payload)
    if not isinstance(result, dict):
        return {
            "tool": "oj_eval",
            "error": f"oj_eval_tool returned non-dict result: {type(result).__name__}",
            "raw_result": result,
        }

    invocation = dict(result.get("tool_invocation") or {})
    invocation.update(
        {
            "framework": "langchain",
            "tool_name": tool.name,
            "tool_type": type(tool).__name__,
        }
    )
    result["tool_invocation"] = invocation
    return result
