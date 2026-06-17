"""Streamlit 可视化入口。

这个文件只负责界面展示和收集用户配置，不直接实现质检逻辑。
真正的 Agent 编排在 `quality_agent.workflow.graph.run_quality_agent` 中完成。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from quality_agent.core.config import LangfuseConfig, ModelConfig, QualityAgentConfig
from quality_agent.standardization import (
    apply_mapping,
    guess_field_mapping,
    guess_mapping_with_llm,
    load_json_or_jsonl,
    load_json_items,
    load_mapping_templates,
    save_mapping_template,
    save_normalization_outputs,
    standard_schema_rows,
)
from quality_agent.workflow.chat_agent import run_chat_quality_agent
from quality_agent.workflow.graph import run_quality_agent
from quality_ui.config_store import UI_CONFIG_FILE, clear_ui_config, load_ui_config, save_ui_config


DATASET_TYPE_LABELS = {
    "math": "客观题",
    "code": "编程题",
    "mixed": "混合题库",
    "auto": "自动识别",
}
RISK_FLAG_LABELS = {
    "dry_run_no_solver_evidence": "演练模式：未调用评测模型",
    "no_solver_evidence": "缺少模型评测证据",
    "low_pass_rate": "通过率偏低",
    "suspect_bad_item": "疑似坏题",
    "completeness_error": "字段缺失或结构错误",
    "completeness_warning": "字段不完整或格式可疑",
    "invalid_samples": "存在无效采样",
    "excluded_from_accuracy": "未计入正确率",
}
REPORT_FILE_LABELS = {
    "quality_report": "quality_report.md（给人看的质检报告）",
    "quality_summary": "quality_summary.json（完整质检结果，给程序读取）",
    "completeness_findings": "completeness_findings.json（字段缺失、格式异常等问题）",
    "review_queue": "review_queue.json（建议人工复核的题目清单）",
    "trace_metadata": "trace_metadata.json（本次运行的模型、路径和追踪信息）",
    "standard_questions": "standard_questions（标准化后的题目数据）",
    "quality_dataset": "quality_dataset（可直接用于质检的兼容数据）",
    "field_mapping": "field_mapping.json（本次字段映射关系）",
    "normalization_report": "normalization_report.json（标准化问题报告）",
}
REPORT_FILE_ORDER = [
    "standard_questions",
    "quality_dataset",
    "field_mapping",
    "normalization_report",
    "quality_report",
    "quality_summary",
    "completeness_findings",
    "review_queue",
    "trace_metadata",
]
MAX_PREVIEW_BYTES = 2 * 1024 * 1024

REPORT_FILE_HELP = {
    "quality_report": "这份报告适合直接给业务或质检人员阅读，包含运行信息、总览、难度分布、风险说明和题目明细。",
    "quality_summary": "这是完整机器结果。普通查看时重点关注：数据集识别结果、质检结果列表、复核数量、警告和错误。",
    "completeness_findings": "这里列出数据集结构问题，例如题干缺失、答案缺失、测试样例异常。为空数组表示暂未发现结构问题。",
    "review_queue": "这里列出建议人工复核的题目。为空数组表示当前规则没有把题目放入复核队列。",
    "trace_metadata": "这里记录本次运行使用的数据集、模型名称、Langfuse 地址和运行目录，方便事后追踪。",
    "standard_questions": "这里是按试题数据模型统一后的 JSON，可作为后续质检或入库前的标准数据。",
    "quality_dataset": "这里保留标准字段，同时补充 oj_eval 运行所需的兼容字段，适合直接送入质检流程。",
    "field_mapping": "这里记录本次原始字段到标准字段的映射关系，确认后可以保存为模板复用。",
    "normalization_report": "这里记录标准化过程中的缺失字段、转换问题和统计信息。",
}


def _check_langfuse_auth(config: LangfuseConfig) -> tuple[bool, str]:
    """检测 Langfuse 配置是否能通过服务端认证，不返回任何密钥内容。"""

    if not config.public_key or not config.secret_key:
        return False, "请先填写 Public Key 和 Secret Key。"
    try:
        from langfuse import Langfuse  # type: ignore
    except Exception:
        return False, "当前环境未安装 Langfuse 依赖，请先安装 agent 依赖。"

    try:
        client = Langfuse(
            public_key=config.public_key,
            secret_key=config.secret_key,
            base_url=config.base_url or None,
            timeout=10,
        )
        ok = bool(client.auth_check())
        try:
            client.flush()
        except Exception:
            pass
        if ok:
            return True, "认证成功：当前 Base URL 与密钥可以连接到 Langfuse。"
        return False, "认证失败：Langfuse 没有接受当前配置。"
    except Exception as exc:
        message = str(exc)
        if "401" in message or "Invalid credentials" in message:
            return False, "认证失败：Public Key、Secret Key 或 Base URL 不匹配。"
        if "ConnectError" in message or "NameResolutionError" in message or "timed out" in message.lower():
            return False, "连接失败：请检查 Base URL 是否正确，以及网络是否能访问 Langfuse。"
        return False, f"检测失败：{type(exc).__name__}"


def _model_form(prefix: str, title: str, defaults: Dict[str, Any]) -> ModelConfig:
    """渲染一个模型配置表单，并返回统一的 ModelConfig。

    prefix 用于隔离 Streamlit widget key，避免“评测模型”和“Agent 模型”
    两组输入控件互相覆盖。
    """

    import streamlit as st

    st.subheader(title)
    base_url = st.text_input(f"{title} Base URL（OpenAI 兼容接口）", value=defaults.get("base_url", ""), key=f"{prefix}_base_url")
    api_key = st.text_input(f"{title} API Key", value=defaults.get("api_key", ""), type="password", key=f"{prefix}_api_key")
    model_name = st.text_input(f"{title} 模型名称", value=defaults.get("model_name", ""), key=f"{prefix}_model")
    col1, col2, col3 = st.columns(3)
    with col1:
        temperature = st.number_input(
            f"{title} 温度",
            min_value=0.0,
            max_value=2.0,
            value=float(defaults.get("temperature", 0.1)),
            step=0.1,
            key=f"{prefix}_temperature",
        )
    with col2:
        top_p = st.number_input(
            f"{title} Top P",
            min_value=0.0,
            max_value=1.0,
            value=float(defaults.get("top_p", 0.95)),
            step=0.05,
            key=f"{prefix}_top_p",
        )
    with col3:
        max_tokens = st.number_input(
            f"{title} 最大 Tokens",
            min_value=1,
            value=int(defaults.get("max_tokens", 1024)),
            step=128,
            key=f"{prefix}_max_tokens",
        )
    return ModelConfig(
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        temperature=float(temperature),
        top_p=float(top_p),
        max_tokens=int(max_tokens),
    )


def _langfuse_form(enabled: bool, defaults: Dict[str, Any]) -> LangfuseConfig:
    """渲染 Langfuse 配置输入。"""

    import streamlit as st

    with st.expander("Langfuse 轨迹配置", expanded=enabled):
        if not enabled:
            st.caption("未启用。打开上方开关后，这里会自动展开并显示 Public Key、Secret Key 和 Base URL。")
            return LangfuseConfig(
                public_key=defaults.get("public_key", ""),
                secret_key=defaults.get("secret_key", ""),
                base_url=defaults.get("base_url", "https://cloud.langfuse.com"),
            )

        st.caption("用于记录 Agent 调用大模型时的链路、输入输出和耗时。")
        public_key = st.text_input(
            "Langfuse Public Key（公钥）",
            value=defaults.get("public_key", ""),
            key="langfuse_public_key",
        )
        secret_key = st.text_input(
            "Langfuse Secret Key（密钥）",
            value=defaults.get("secret_key", ""),
            type="password",
            key="langfuse_secret_key",
        )
        base_url = st.text_input(
            "Langfuse Base URL（服务地址）",
            value=defaults.get("base_url", "https://cloud.langfuse.com"),
            key="langfuse_base_url",
        )

        if not public_key or not secret_key:
            st.warning("已启用 Langfuse，请填写 Public Key 和 Secret Key。")
        elif st.button("检测 Langfuse 配置", key="check_langfuse_auth", use_container_width=True):
            ok, message = _check_langfuse_auth(
                LangfuseConfig(public_key=public_key, secret_key=secret_key, base_url=base_url)
            )
            if ok:
                st.success(message)
            else:
                st.error(message)

    return LangfuseConfig(public_key=public_key, secret_key=secret_key, base_url=base_url)


def _build_ui_config(
    *,
    dataset: str,
    mode: str,
    samples: int,
    output_dir: str,
    dry_run: bool,
    enable_llm_analysis: bool,
    enable_langfuse: bool,
    use_langgraph: bool,
    save_secrets: bool,
    solver_model: ModelConfig,
    agent_model: ModelConfig,
    langfuse: LangfuseConfig,
) -> Dict[str, Any]:
    """把当前页面状态整理成本地配置文件结构。"""

    return {
        "dataset": dataset,
        "mode": mode,
        "samples": samples,
        "output_dir": output_dir,
        "dry_run": dry_run,
        "enable_llm_analysis": enable_llm_analysis,
        "enable_langfuse": enable_langfuse,
        "use_langgraph": use_langgraph,
        "save_secrets": save_secrets,
        "solver": {
            "base_url": solver_model.base_url,
            "api_key": solver_model.api_key if save_secrets else "",
            "model_name": solver_model.model_name,
            "temperature": solver_model.temperature,
            "top_p": solver_model.top_p,
            "max_tokens": solver_model.max_tokens,
        },
        "agent": {
            "base_url": agent_model.base_url,
            "api_key": agent_model.api_key if save_secrets else "",
            "model_name": agent_model.model_name,
            "temperature": agent_model.temperature,
            "top_p": agent_model.top_p,
            "max_tokens": agent_model.max_tokens,
        },
        "langfuse": {
            "public_key": langfuse.public_key,
            "secret_key": langfuse.secret_key if save_secrets else "",
            "base_url": langfuse.base_url,
        },
    }


def _mode_index(mode: str) -> int:
    """根据已保存配置返回 selectbox 的默认下标。"""

    modes = ["auto", "math", "code", "mixed"]
    return modes.index(mode) if mode in modes else 0


def _model_from_session(prefix: str, defaults: Dict[str, Any]) -> ModelConfig:
    """从 Streamlit 会话状态读取模型配置。"""

    import streamlit as st

    return ModelConfig(
        base_url=st.session_state.get(f"{prefix}_base_url", defaults.get("base_url", "")),
        api_key=st.session_state.get(f"{prefix}_api_key", defaults.get("api_key", "")),
        model_name=st.session_state.get(f"{prefix}_model", defaults.get("model_name", "")),
        temperature=float(st.session_state.get(f"{prefix}_temperature", defaults.get("temperature", 0.1))),
        top_p=float(st.session_state.get(f"{prefix}_top_p", defaults.get("top_p", 0.95))),
        max_tokens=int(st.session_state.get(f"{prefix}_max_tokens", defaults.get("max_tokens", 1024))),
    )


_PROGRESS_STEP_PERCENT = {
    "initialize": 5,
    "workflow": 8,
    "chat_agent": 10,
    "react_tool": 15,
    "chat_fallback": 15,
    "inspect_dataset": 25,
    "finalize_trace_metadata": 35,
    "run_eval": 55,
    "score_quality": 70,
    "llm_analysis": 85,
    "write_report": 100,
}

_STATUS_TEXT = {
    "running": "运行中",
    "completed": "完成",
    "skipped": "跳过",
    "failed": "失败",
}


def _event_row(event: Dict[str, Any]) -> Dict[str, str]:
    """把内部事件格式转换成页面表格里的中文列。"""

    status = str(event.get("status", ""))
    return {
        "时间": str(event.get("time", "")),
        "阶段": str(event.get("label") or event.get("step", "")),
        "状态": _STATUS_TEXT.get(status, status),
        "说明": str(event.get("message", "")),
    }


def _progress_percent(events: list[Dict[str, Any]]) -> int:
    """根据最近事件估算进度条百分比。"""

    if not events:
        return 0
    latest = events[-1]
    if latest.get("status") == "failed":
        return min(100, max(5, _PROGRESS_STEP_PERCENT.get(str(latest.get("step")), 10)))
    return min(100, max(_PROGRESS_STEP_PERCENT.get(str(item.get("step")), 10) for item in events))


def _live_log_text(events: list[Dict[str, Any]]) -> str:
    """生成适合 code block 展示的实时日志文本。"""

    lines = []
    for event in events:
        row = _event_row(event)
        lines.append(f"[{row['时间']}] {row['状态']} | {row['阶段']} | {row['说明']}")
    return "\n".join(lines[-80:])


def _all_solver_metrics_empty(results: list[Dict[str, Any]]) -> bool:
    """判断当前结果是否没有真实 solver 采样指标。"""

    if not results:
        return False
    metric_fields = ("sample_count", "valid_sample_count", "passed_samples", "failed_samples", "sample_pass_rate")
    return all(all(float(item.get(field, 0) or 0) == 0 for field in metric_fields) for item in results)


def _bool_text(value: Any) -> str:
    """把真假值显示成中文。"""

    return "是" if bool(value) else "否"


def _dataset_type_text(value: Any) -> str:
    """把内部题库类型显示成中文。"""

    raw = str(value or "")
    return DATASET_TYPE_LABELS.get(raw, raw or "未知")


def _risk_flags_text(flags: list[str] | tuple[str, ...] | None) -> str:
    """把内部风险标签显示成中文说明。"""

    if not flags:
        return "暂无明显风险"
    return "；".join(RISK_FLAG_LABELS.get(str(flag), str(flag)) for flag in flags)


def _pass_rate_display(item: Dict[str, Any]) -> str:
    """没有模型证据时，不把 0% 展示成真实通过率。"""

    flags = item.get("risk_flags", [])
    if "dry_run_no_solver_evidence" in flags or "no_solver_evidence" in flags:
        return "无评测数据"
    return f"{float(item.get('sample_pass_rate', 0.0) or 0.0) * 100:.2f}%"


def _quality_result_rows(results: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """把内部质检结果转换成面向业务阅读的中文表格。"""

    return [
        {
            "题目 ID": item.get("problem_id", ""),
            "题型": item.get("question_type", "") or "未标注",
            "采样次数": item.get("sample_count", 0),
            "有效采样": item.get("valid_sample_count", 0),
            "通过数": item.get("passed_samples", 0),
            "失败数": item.get("failed_samples", 0),
            "通过率": _pass_rate_display(item),
            "难度判断": item.get("agent_difficulty", ""),
            "建议人工复核": _bool_text(item.get("need_human_review")),
            "风险说明": _risk_flags_text(item.get("risk_flags", [])),
        }
        for item in results
    ]


def _json_report_summary(selected: str, data: Any) -> None:
    """在原始 JSON 前面补一层中文解释，降低阅读门槛。"""

    import streamlit as st

    if selected == "quality_summary" and isinstance(data, dict):
        dataset_summary = data.get("dataset_summary", {})
        quality_results = data.get("quality_results", [])
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("题库类型", _dataset_type_text(dataset_summary.get("detected_type")))
        c2.metric("题目总数", dataset_summary.get("total_items", 0))
        c3.metric("质检结果数", len(quality_results))
        c4.metric("复核数量", data.get("review_queue_count", 0))
        if quality_results:
            st.markdown("**质检结果预览**")
            st.dataframe(_quality_result_rows(quality_results), use_container_width=True, hide_index=True)
        if data.get("warnings"):
            st.warning(f"本次运行有 {len(data.get('warnings', []))} 条警告，详情见原始 JSON。")
        if data.get("errors"):
            st.error(f"本次运行有 {len(data.get('errors', []))} 条错误，详情见原始 JSON。")
        return

    if selected == "review_queue" and isinstance(data, list):
        if not data:
            st.success("当前没有需要人工复核的题目。")
        else:
            st.markdown("**建议人工复核的题目**")
            st.dataframe(_quality_result_rows(data), use_container_width=True, hide_index=True)
        return

    if selected == "completeness_findings" and isinstance(data, list):
        if not data:
            st.success("当前没有发现字段缺失或结构异常。")
        else:
            st.markdown("**完整性问题列表**")
            st.dataframe(data, use_container_width=True, hide_index=True)
        return

    if selected == "trace_metadata" and isinstance(data, dict):
        rows = [
            {"项目": "数据集路径", "内容": data.get("dataset_path", "")},
            {"项目": "题库类型", "内容": _dataset_type_text(data.get("dataset_type", ""))},
            {"项目": "每题采样次数", "内容": data.get("samples", "")},
            {"项目": "评测模型", "内容": data.get("solver_model", "")},
            {"项目": "Agent 模型", "内容": data.get("agent_model", "")},
            {"项目": "Langfuse 地址", "内容": data.get("langfuse_base_url", "")},
            {"项目": "运行目录", "内容": data.get("run_dir", "")},
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)


def _ordered_report_items(report_paths: Dict[str, Any]) -> list[tuple[str, Path]]:
    """按固定业务顺序整理报告文件路径。"""

    items: list[tuple[str, Path]] = []
    for name in REPORT_FILE_ORDER:
        raw_path = report_paths.get(name)
        if raw_path:
            items.append((name, Path(str(raw_path))))
    for name, raw_path in report_paths.items():
        if name not in REPORT_FILE_LABELS and raw_path:
            items.append((name, Path(str(raw_path))))
    return items


def _read_report_file(path: Path) -> tuple[str | None, str | None]:
    """读取报告文件；返回内容和错误信息。"""

    try:
        if not path.exists():
            return None, f"文件不存在：{path}"
        if path.is_dir():
            return None, f"这是目录，不是文件：{path}"
        if path.stat().st_size > MAX_PREVIEW_BYTES:
            size_mb = path.stat().st_size / 1024 / 1024
            return None, f"文件较大（{size_mb:.2f} MB），为避免页面卡顿暂不预览。"
        return path.read_text(encoding="utf-8"), None
    except UnicodeDecodeError:
        return None, "文件不是 UTF-8 文本，无法在页面内直接预览。"
    except OSError as exc:
        return None, f"读取失败：{exc}"


def _render_report_files(report_paths: Dict[str, Any] | None, *, key_prefix: str = "report") -> None:
    """在 UI 中展示报告路径，并支持直接打开查看内容。"""

    import streamlit as st

    report_items = _ordered_report_items(report_paths or {})
    if not report_items:
        st.info("暂无报告文件。完成一次质检后，这里会显示可打开的报告产物。")
        return

    rows = [
        {
            "文件": REPORT_FILE_LABELS.get(name, name),
            "路径": str(path),
            "状态": "可打开" if path.exists() and path.is_file() else "未找到",
        }
        for name, path in report_items
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    options = [name for name, _ in report_items]
    selected = st.selectbox(
        "选择要打开的文件",
        options=options,
        format_func=lambda name: REPORT_FILE_LABELS.get(name, name),
        key=f"{key_prefix}_file_selector",
    )
    selected_path = dict(report_items)[selected]
    content, error = _read_report_file(selected_path)

    with st.expander(f"打开文件：{REPORT_FILE_LABELS.get(selected, selected)}", expanded=True):
        st.caption(str(selected_path))
        help_text = REPORT_FILE_HELP.get(selected)
        if help_text:
            st.info(help_text)
        if error:
            st.warning(error)
            return

        if selected_path.suffix.lower() == ".json":
            try:
                data = json.loads(content or "{}")
                _json_report_summary(selected, data)
                with st.expander("原始 JSON（给程序和调试使用）", expanded=False):
                    st.json(data)
            except json.JSONDecodeError:
                st.code(content or "", language="json")
        elif selected_path.suffix.lower() == ".jsonl":
            try:
                data = [json.loads(line) for line in (content or "").splitlines() if line.strip()]
                st.caption(f"JSONL 已解析为 {len(data)} 行，每行对应一道题。")
                with st.expander("解析后的 JSONL", expanded=True):
                    st.json(data)
                with st.expander("原始 JSONL（给程序和调试使用）", expanded=False):
                    st.code(content or "", language="json")
            except json.JSONDecodeError:
                st.code(content or "", language="json")
        elif selected_path.suffix.lower() in {".md", ".markdown"}:
            st.markdown(content or "")
        else:
            st.code(content or "", language="text")


def _create_live_event_recorder(title: str, session_key: str) -> tuple[list[Dict[str, Any]], Any]:
    """创建一个实时日志区域，并返回可传给 Agent 的事件回调。"""

    import streamlit as st

    events: list[Dict[str, Any]] = []
    st.session_state[session_key] = events

    with st.expander(title, expanded=True):
        progress_slot = st.empty()
        table_slot = st.empty()
        detail_slot = st.empty()

    def render() -> None:
        if not events:
            progress_slot.progress(0)
            table_slot.info("等待工作流开始。")
            detail_slot.empty()
            return

        latest = _event_row(events[-1])
        progress_text = f"{latest['阶段']}：{latest['状态']}。{latest['说明']}"
        try:
            progress_slot.progress(_progress_percent(events), text=progress_text)
        except TypeError:
            progress_slot.progress(_progress_percent(events))
        table_slot.dataframe([_event_row(event) for event in events], use_container_width=True, hide_index=True)
        detail_slot.code(_live_log_text(events), language="text")

    def on_event(event: Dict[str, Any]) -> None:
        events.append(dict(event))
        st.session_state[session_key] = events
        render()

    render()
    return events, on_event


def _business_nav() -> str:
    """渲染顶部业务导航。"""

    import streamlit as st

    return st.radio(
        "业务导航",
        ["数据标准化", "表单质检", "Agent 会话", "报告结果", "模型配置"],
        horizontal=True,
        label_visibility="collapsed",
    )


def _save_uploaded_json(uploaded_file: Any, output_dir: str) -> Path:
    """把上传的 JSON/JSONL 保存到本地运行目录，并做最小格式校验。"""

    raw = uploaded_file.getvalue()
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(uploaded_file.name or "dataset.json").name)
    target_dir = Path(output_dir) / "uploads"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{safe_name}"
    target.write_bytes(raw)
    try:
        load_json_or_jsonl(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def _render_standard_schema() -> None:
    """展示标准试题 JSON 字段要求。"""

    import streamlit as st

    with st.expander("标准 JSON 字段要求", expanded=True):
        st.dataframe(standard_schema_rows(), use_container_width=True, hide_index=True)
        st.markdown("**标准 JSON 外层格式**")
        st.code(
            json.dumps(
                {
                    "questions": [
                        {
                            "id": "题目唯一 ID",
                            "type": "single_choice",
                            "question": "题干内容",
                            "options": [{"label": "A", "text": "选项内容"}],
                            "answer": "A",
                            "analysis": "答案解析",
                            "knowledge_points": [{"id": "kp1", "name": "知识点", "path": "知识点"}],
                            "language": "zh",
                            "difficulty": "中",
                            "subject": "数学",
                            "grade": "大学",
                            "is_contest": False,
                            "contest_type": "",
                            "source": "自建题库",
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            language="json",
        )


def _standardization_dataset_path(uploaded_file: Any, dataset: str, output_dir: str) -> Path:
    """解析数据标准化页面当前使用的原始 JSON/JSONL。"""

    import streamlit as st

    if not uploaded_file:
        return Path(dataset)
    raw = uploaded_file.getvalue()
    file_key = f"{uploaded_file.name}:{len(raw)}:{hashlib.sha256(raw).hexdigest()[:16]}"
    if st.session_state.get("standardization_upload_key") != file_key:
        dataset_path = _save_uploaded_json(uploaded_file, output_dir)
        st.session_state["standardization_upload_key"] = file_key
        st.session_state["standardization_dataset_path"] = str(dataset_path)
    return Path(st.session_state["standardization_dataset_path"])


def _reset_mapping_widget_values() -> None:
    """清空字段映射下拉框状态，让新的规则或大模型建议能立刻显示出来。"""

    import streamlit as st

    for row in standard_schema_rows():
        st.session_state.pop(f"mapping_{row['字段名']}", None)


def _mapping_editor(source_fields: list[str], mapping: Dict[str, str], confidence: Dict[str, float] | None = None) -> Dict[str, str]:
    """渲染人工确认映射表，并返回确认后的映射。"""

    import streamlit as st

    confirmed: Dict[str, str] = {}
    options = [""] + source_fields
    schema = standard_schema_rows()
    st.markdown("**字段映射确认**")
    st.caption("左侧是标准字段，右侧请选择对应的原始 JSON 字段。空值表示暂不映射。")
    for row in schema:
        target = row["字段名"]
        default_source = mapping.get(target, "")
        index = options.index(default_source) if default_source in options else 0
        cols = st.columns([1.1, 1.2, 2.2, 0.8])
        cols[0].markdown(f"`{target}`")
        cols[1].write(row["中文标签"])
        selected = cols[2].selectbox(
            f"{target} 映射字段",
            options=options,
            index=index,
            key=f"mapping_{target}",
            label_visibility="collapsed",
        )
        score = "" if confidence is None or target not in confidence else f"{float(confidence[target]) * 100:.0f}%"
        cols[3].write(score)
        if selected:
            confirmed[target] = selected
    return confirmed


def _render_standardization_page(
    *,
    dataset: str,
    output_dir: str,
    agent_model: ModelConfig,
    enable_langfuse: bool,
    langfuse: LangfuseConfig,
) -> None:
    """数据标准化前置页面。"""

    import streamlit as st

    st.subheader("数据标准化")
    st.caption("先把不同来源 JSON 映射为统一试题数据模型，确认后再进入质检。")
    _render_standard_schema()

    uploaded_file = st.file_uploader("上传原始 JSON / JSONL", type=["json", "jsonl"], key="standardization_json")
    try:
        source_path = _standardization_dataset_path(uploaded_file, dataset, output_dir) if uploaded_file else Path(dataset)
    except Exception as exc:
        st.error(f"上传文件不是合法 JSON/JSONL：{exc}")
        source_path = Path(dataset)
    st.caption(f"当前原始 JSON/JSONL：`{source_path}`")

    templates = load_mapping_templates()
    template_names = ["不使用模板"] + sorted(templates.keys())
    selected_template = st.selectbox("套用字段映射模板", template_names, key="standardization_template")

    if st.button("读取 JSON/JSONL 并生成规则映射建议", type="primary", key="load_standardization_source"):
        try:
            items, raw_shape = load_json_items(source_path)
            suggestion = guess_field_mapping(items)
            mapping = dict(suggestion.mapping)
            confidence = dict(suggestion.confidence)
            if selected_template != "不使用模板":
                template_mapping = templates.get(selected_template, {}).get("mapping", {})
                if isinstance(template_mapping, dict):
                    mapping.update({str(k): str(v) for k, v in template_mapping.items() if v})
                    confidence.update({str(k): 1.0 for k in template_mapping.keys()})
            st.session_state["standardization_items"] = items
            st.session_state["standardization_raw_shape"] = raw_shape
            st.session_state["standardization_source_fields"] = suggestion.source_fields
            st.session_state["standardization_mapping"] = mapping
            st.session_state["standardization_confidence"] = confidence
            st.session_state.pop("standardization_result", None)
            st.session_state.pop("standardization_paths", None)
            _reset_mapping_widget_values()
            st.success(f"已读取 {len(items)} 道题，生成字段映射建议。")
        except Exception as exc:
            st.error(f"读取或识别失败：{exc}")

    items = st.session_state.get("standardization_items", [])
    source_fields = st.session_state.get("standardization_source_fields", [])
    if not items:
        st.info("请先读取原始 JSON/JSONL。")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("题目数", len(items))
    c2.metric("原始字段数", len(source_fields))
    c3.metric("已映射字段", len(st.session_state.get("standardization_mapping", {})))

    with st.expander("原始 JSON 结构预览", expanded=False):
        st.json(
            {
                "raw_shape": st.session_state.get("standardization_raw_shape", {}),
                "source_fields": source_fields,
                "sample_items": items[:2],
            }
        )

    if st.button("使用大模型辅助猜测字段映射", key="llm_guess_mapping"):
        try:
            llm_result = guess_mapping_with_llm(
                source_fields=source_fields,
                sample_items=items[:3],
                model_config=agent_model,
                enable_langfuse=enable_langfuse,
                langfuse_config=langfuse,
                metadata={
                    "agent_mode": "field_mapping",
                    "dataset_path": str(source_path),
                    "source_field_count": len(source_fields),
                },
            )
            mapping = dict(st.session_state.get("standardization_mapping", {}))
            mapping.update(llm_result.get("mapping", {}))
            confidence = dict(st.session_state.get("standardization_confidence", {}))
            confidence.update(llm_result.get("confidence", {}))
            st.session_state["standardization_mapping"] = mapping
            st.session_state["standardization_confidence"] = confidence
            _reset_mapping_widget_values()
            st.success("大模型映射建议已合并，请在下方人工确认。")
        except Exception as exc:
            st.error(f"大模型映射失败：{exc}")

    confirmed_mapping = _mapping_editor(
        source_fields,
        st.session_state.get("standardization_mapping", {}),
        st.session_state.get("standardization_confidence", {}),
    )
    st.session_state["standardization_mapping"] = confirmed_mapping

    template_name = st.text_input("模板名称", value=selected_template if selected_template != "不使用模板" else "", key="mapping_template_name")
    output_format_label = st.selectbox(
        "标准化输出格式",
        ["JSON（带 questions/cases 外层）", "JSONL（每行一道题）"],
        key="standardization_output_format",
    )
    output_format = "jsonl" if output_format_label.startswith("JSONL") else "json"
    col_save_template, col_generate = st.columns(2)
    with col_save_template:
        if st.button("保存映射模板", key="save_mapping_template", use_container_width=True):
            if not confirmed_mapping:
                st.warning("当前没有可保存的映射。")
            else:
                path = save_mapping_template(template_name or "default", confirmed_mapping)
                st.success(f"已保存模板：{path}")
    with col_generate:
        if st.button("生成标准文件", key="generate_standard_json", use_container_width=True):
            result = apply_mapping(items, confirmed_mapping)
            out_dir = Path(output_dir) / "standardized" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            paths = save_normalization_outputs(result, out_dir, output_format=output_format)
            st.session_state["standardization_result"] = result
            st.session_state["standardization_paths"] = paths
            st.success(f"标准化文件已生成，题目数据格式：{output_format.upper()}。")

    result = st.session_state.get("standardization_result")
    paths = st.session_state.get("standardization_paths", {})
    if result:
        st.subheader("标准化结果")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("标准题目数", len(result.standard_items))
        c2.metric("映射字段数", len(result.mapping))
        c3.metric("字段缺失数", result.report.get("missing_required_count", 0))
        c4.metric("空值字段数", result.report.get("empty_required_count", 0))
        st.caption("字段缺失表示标准字段不存在；空值字段表示字段已存在但当前题型或原始数据没有内容，可按业务规则决定是否接受。")
        st.dataframe(result.standard_items[:20], use_container_width=True, hide_index=True)
        _render_report_files(paths, key_prefix="standardization_output")
        quality_path = paths.get("quality_dataset") or paths.get("standard_questions")
        if quality_path and st.button("将可质检文件设为左侧质检数据集", key="use_standard_json_for_quality"):
            st.session_state["pending_dataset_path"] = str(quality_path)
            st.session_state["pending_dataset_notice"] = "已设为质检数据集，请切换到“表单质检”后开始质检。"
            st.rerun()


def _chat_dataset_path(uploaded_file: Any, dataset: str, output_dir: str) -> Path:
    """解析 Agent 会话当前使用的 JSON/JSONL 数据集路径。"""

    import streamlit as st

    if not uploaded_file:
        return Path(dataset)

    raw = uploaded_file.getvalue()
    file_key = f"{uploaded_file.name}:{len(raw)}:{hashlib.sha256(raw).hexdigest()[:16]}"
    if st.session_state.get("chat_upload_key") != file_key:
        dataset_path = _save_uploaded_json(uploaded_file, output_dir)
        st.session_state["chat_upload_key"] = file_key
        st.session_state["chat_dataset_path"] = str(dataset_path)
    return Path(st.session_state["chat_dataset_path"])


def _quality_config(
    *,
    dataset_path: Path,
    user_instruction: str,
    mode: str,
    samples: int,
    output_dir: str,
    dry_run: bool,
    enable_llm_analysis: bool,
    enable_langfuse: bool,
    solver_model: ModelConfig,
    agent_model: ModelConfig,
    langfuse: LangfuseConfig,
) -> QualityAgentConfig:
    """把 UI 当前状态转换为一次 Agent 运行配置。"""

    return QualityAgentConfig(
        dataset_path=dataset_path,
        user_instruction=user_instruction,
        mode=mode,
        samples=int(samples),
        output_dir=Path(output_dir),
        dry_run=dry_run,
        enable_llm_analysis=enable_llm_analysis,
        enable_langfuse=enable_langfuse,
        solver_model=solver_model,
        agent_model=agent_model,
        langfuse=langfuse,
    )


def _render_quality_state(state: Dict[str, Any] | None) -> None:
    """展示一次质检工作流的结果，默认只保留摘要、运行过程和输出文件。"""

    import streamlit as st

    if not state:
        st.info("请先配置数据集路径和模型参数，然后点击“开始质检”。首次试用建议保持 Dry run。")
        return

    results = state.get("quality_results", [])
    review_queue = state.get("review_queue", [])
    dry_run_only = bool(results) and all("dry_run_no_solver_evidence" in item.get("risk_flags", []) for item in results)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("数据集类型", _dataset_type_text(state.get("dataset_type", "")))
    c2.metric("题目总数", state.get("dataset_summary", {}).get("total_items", 0))
    c3.metric("质检结果数", len(results))
    c4.metric("复核队列", len(review_queue))

    if dry_run_only:
        st.warning("当前结果来自 Dry run，只完成结构检查，没有调用评测模型；关闭左侧 Dry run 后才会得到正确率和难度判定。")
    elif _all_solver_metrics_empty(results):
        eval_summary = state.get("eval_summary", {})
        error = eval_summary.get("error") or "评测工具没有返回题目级采样结果。"
        st.warning(f"当前表格没有真实采样指标：{error}")
    config = state.get("config")
    if config and getattr(config, "enable_langfuse", False):
        eval_summary = state.get("eval_summary", {})
        if eval_summary.get("dry_run"):
            st.info("Langfuse 提示：本次是 Dry run，没有调用评测模型；如果也没有触发 Agent 模型分析，Langfuse 里不会出现新的模型轨迹。")
        elif not state.get("llm_findings"):
            st.info("Langfuse 提示：本次没有 Agent 归因结果；关闭 Dry run 后，oj_eval 底层评测模型调用也会作为 generation 上传到 Langfuse。")

    st.subheader("运行过程")
    _render_run_process(state)

    st.subheader("输出文件")
    _render_report_files(state.get("report_paths"), key_prefix="quality_report")

    with st.expander("高级明细：质检结果和复核队列", expanded=False):
        st.markdown("**质检结果**")
        st.dataframe(_quality_result_rows(results), use_container_width=True, hide_index=True)
        st.markdown("**人工复核队列**")
        if review_queue:
            st.dataframe(_quality_result_rows(review_queue), use_container_width=True, hide_index=True)
        else:
            st.success("当前没有需要人工复核的题目。")
        st.markdown("**原始质检结果 JSON**")
        st.json(results)
        st.markdown("**原始复核队列 JSON**")
        st.json(review_queue)


def _render_run_process(state: Dict[str, Any] | None) -> None:
    """展示工作流运行过程和关键中间状态。"""

    import streamlit as st

    if not state:
        return

    config = state.get("config")
    eval_summary = state.get("eval_summary", {})
    report_paths = state.get("report_paths", {})
    tool_invocation = eval_summary.get("tool_invocation", {})
    process_rows = [
        {"步骤": "1. 读取配置", "结果": "完成", "说明": f"数据集：{getattr(config, 'dataset_path', '')}"},
        {"步骤": "2. 数据集识别", "结果": state.get("dataset_type", ""), "说明": f"题目数：{state.get('dataset_summary', {}).get('total_items', 0)}"},
        {"步骤": "3. 完整性检查", "结果": "完成", "说明": f"发现项：{len(state.get('completeness_findings', []))}"},
        {"步骤": "4. 调用评测工具", "结果": eval_summary.get("tool", "oj_eval"), "说明": "Dry run 未调用模型" if eval_summary.get("dry_run") else "已请求评测模型"},
        {"步骤": "5. 规则评分", "结果": "完成", "说明": f"质检结果：{len(state.get('quality_results', []))}"},
        {"步骤": "6. Agent 归因", "结果": "完成" if state.get("llm_findings") else "未触发或无结果", "说明": f"归因数：{len(state.get('llm_findings', []))}"},
        {"步骤": "7. 写入报告", "结果": "完成" if report_paths else "未完成", "说明": f"输出文件：{len(report_paths)}"},
    ]

    with st.expander("运行过程明细", expanded=True):
        st.dataframe(process_rows, use_container_width=True, hide_index=True)
        if state.get("live_events"):
            st.markdown("**实时事件记录**")
            st.dataframe([_event_row(event) for event in state.get("live_events", [])], use_container_width=True, hide_index=True)
        st.markdown("**本次运行目录**")
        st.code(str(state.get("run_dir", "")), language="text")

    with st.expander("运行调试信息", expanded=False):
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**任务配置（脱敏）**")
            st.json(config.redacted() if config else {})
        with col_b:
            st.markdown("**工具调用信息**")
            st.json(
                {
                    "tool": eval_summary.get("tool", ""),
                    "dry_run": eval_summary.get("dry_run", False),
                    "returncode": eval_summary.get("returncode", ""),
                    "tool_invocation": tool_invocation,
                    "mode": eval_summary.get("mode", ""),
                    "total_problems": eval_summary.get("total_problems", ""),
                }
            )

        if eval_summary.get("command"):
            st.markdown("**实际执行命令（已脱敏）**")
            st.code(" ".join(str(item) for item in eval_summary.get("command", [])), language="powershell")
        if eval_summary.get("commands"):
            st.markdown("**实际执行命令（混合题库，已脱敏）**")
            st.json(eval_summary.get("commands", {}))

        if eval_summary.get("stdout"):
            st.markdown("**工具 stdout 摘要**")
            st.code(str(eval_summary.get("stdout", ""))[-4000:], language="text")
        if eval_summary.get("stderr"):
            st.markdown("**工具 stderr 摘要**")
            st.code(str(eval_summary.get("stderr", ""))[-4000:], language="text")

        if state.get("warnings"):
            st.markdown("**警告**")
            st.json(state.get("warnings", []))
        if state.get("errors"):
            st.markdown("**错误**")
            st.json(state.get("errors", []))


def _render_chat_process(summary: Dict[str, Any]) -> None:
    """展示 Agent 会话工具过程。"""

    import streamlit as st

    if summary.get("conversation_only"):
        st.info("本轮只是普通对话，没有调用质检工具。")
        return

    with st.expander("Agent 运行过程", expanded=False):
        st.dataframe(summary.get("process_steps", []), use_container_width=True, hide_index=True)
        if summary.get("live_events"):
            st.markdown("**实时事件记录**")
            st.dataframe([_event_row(event) for event in summary.get("live_events", [])], use_container_width=True, hide_index=True)
        st.markdown("**运行目录**")
        st.code(str(summary.get("run_dir", "")), language="text")
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**工具摘要**")
            st.json(summary.get("tool_summary", {}))
        with col_b:
            st.markdown("**报告路径**")
            st.json(summary.get("report_paths", {}))
        st.markdown("**报告文件查看**")
        _render_report_files(summary.get("report_paths"), key_prefix="chat_report")
        if summary.get("warnings"):
            st.markdown("**警告**")
            st.json(summary.get("warnings", []))
        if summary.get("errors"):
            st.markdown("**错误**")
            st.json(summary.get("errors", []))


def _render_collapsed_json(title: str, data: Dict[str, Any]) -> None:
    """把较长的原始 JSON 放进默认收起的调试框。"""

    import streamlit as st

    with st.expander(title, expanded=False):
        st.json(data)


def _langfuse_trace_expectation(
    *,
    enable_langfuse: bool,
    dry_run: bool,
    enable_llm_analysis: bool,
    langfuse: LangfuseConfig,
) -> tuple[str, list[str]]:
    """解释当前配置下 Langfuse 什么时候会产生 trace。"""

    if not enable_langfuse:
        return "未启用", ["当前没有打开 Langfuse 轨迹记录。"]
    if not langfuse.public_key or not langfuse.secret_key:
        return "配置不完整", ["请同时填写 Langfuse Public Key 和 Secret Key。"]

    notes = [
        "已启用 Langfuse，并且密钥字段已填写。",
        "Agent 模型调用和 oj_eval 底层评测模型调用都会进入 Langfuse。",
    ]
    if dry_run:
        notes.append("当前开启 Dry run，评测模型不会被调用，因此不会产生评测模型 trace。")
    else:
        notes.append("当前关闭 Dry run，评测工具会调用评测模型，并把每次 solver 请求作为 generation 上传到 Langfuse。")
    if enable_llm_analysis:
        notes.append("Agent 模型分析开启后，只有出现需要复核的题目时才会调用 Agent 模型并产生 trace。")
    else:
        notes.append("Agent 模型分析未开启，表单质检流程通常不会产生 Agent 模型 trace。")
    notes.append("Agent 会话页中，只要实际调用 Agent 模型理解对话或 ReAct 工具，才会产生 trace；纯本地兜底回复不会产生 trace。")
    return "已启用，等待可追踪模型调用", notes


def _render_langfuse_expectation(
    *,
    enable_langfuse: bool,
    dry_run: bool,
    enable_llm_analysis: bool,
    langfuse: LangfuseConfig,
) -> None:
    """在页面上显示 Langfuse 追踪预期，避免把配置成功误认为已产生 trace。"""

    import streamlit as st

    status, notes = _langfuse_trace_expectation(
        enable_langfuse=enable_langfuse,
        dry_run=dry_run,
        enable_llm_analysis=enable_llm_analysis,
        langfuse=langfuse,
    )
    if not enable_langfuse:
        return
    with st.expander("Langfuse 追踪状态说明", expanded=True):
        st.markdown(f"**当前状态：{status}**")
        for note in notes:
            st.write(f"- {note}")


def main() -> None:
    """启动中文可视化控制台。"""

    try:
        import streamlit as st
    except Exception as exc:
        raise RuntimeError("Streamlit is not installed. Install with: pip install -e .[agent]") from exc

    # 页面只作为本地 demo 控制台使用，宽屏布局更适合展示结果表格。
    st.set_page_config(page_title="难题质检智能体", layout="wide")
    st.title("难题质检智能体")
    st.caption("面向 OJ 编程题与客观题数据集的工作流型质检 Agent。")

    saved_config = load_ui_config()
    pending_dataset_path = st.session_state.pop("pending_dataset_path", "")
    if pending_dataset_path:
        st.session_state["dataset_path"] = pending_dataset_path
    elif "dataset_path" not in st.session_state:
        st.session_state["dataset_path"] = saved_config.get("dataset", "cases/math/cases_math_small.json")

    with st.sidebar:
        st.header("任务配置")
        dataset = st.text_input("数据集 JSON 路径", key="dataset_path")
        mode = st.selectbox(
            "质检模式",
            options=["auto", "math", "code", "mixed"],
            index=_mode_index(str(saved_config.get("mode", "auto"))),
            format_func={
                "auto": "自动识别",
                "math": "客观题",
                "code": "编程题",
                "mixed": "混合题库",
            }.get,
        )
        samples = st.number_input("每题采样次数", min_value=1, value=int(saved_config.get("samples", 4)), step=1)
        output_dir = st.text_input("输出目录", value=saved_config.get("output_dir", "runs/quality_agent"))
        dry_run = st.checkbox("仅检查结构，不调用模型（Dry run）", value=bool(saved_config.get("dry_run", True)))
        enable_llm_analysis = st.checkbox("启用 Agent 模型分析", value=bool(saved_config.get("enable_llm_analysis", False)))
        st.divider()
        enable_langfuse = st.checkbox("启用 Langfuse 轨迹记录", value=bool(saved_config.get("enable_langfuse", False)))
        langfuse = _langfuse_form(enable_langfuse, saved_config.get("langfuse", {}))
        st.divider()
        use_langgraph = st.checkbox("优先使用 LangGraph 编排", value=bool(saved_config.get("use_langgraph", True)))
        save_secrets = st.checkbox(
            "保存 API Key / Secret 到本机配置文件（明文）",
            value=bool(saved_config.get("save_secrets", False)),
        )
        if save_secrets:
            st.caption("密钥会写入本机配置文件，请勿把该文件提交或发给他人。")

    section = _business_nav()
    if dry_run:
        st.warning("Dry run 已开启：本次只做结构检查，不调用评测模型；报告中的难度会显示为“未评测”。")
    _render_langfuse_expectation(
        enable_langfuse=enable_langfuse,
        dry_run=dry_run,
        enable_llm_analysis=enable_llm_analysis,
        langfuse=langfuse,
    )
    pending_dataset_notice = st.session_state.pop("pending_dataset_notice", "")
    if pending_dataset_notice:
        st.success(pending_dataset_notice)

    if section == "模型配置":
        col_solver, col_agent = st.columns(2)
        with col_solver:
            st.markdown("**评测模型**：负责做题、生成代码、统计正确率。")
            solver_model = _model_form("solver", "评测模型", saved_config.get("solver", {}))
        with col_agent:
            st.markdown("**Agent 模型**：负责归因解释、复核建议和报告总结。")
            agent_model = _model_form("agent", "Agent 模型", saved_config.get("agent", {}))
    else:
        solver_model = _model_from_session("solver", saved_config.get("solver", {}))
        agent_model = _model_from_session("agent", saved_config.get("agent", {}))

    current_ui_config = _build_ui_config(
        dataset=dataset,
        mode=mode,
        samples=int(samples),
        output_dir=output_dir,
        dry_run=dry_run,
        enable_llm_analysis=enable_llm_analysis,
        enable_langfuse=enable_langfuse,
        use_langgraph=use_langgraph,
        save_secrets=save_secrets,
        solver_model=solver_model,
        agent_model=agent_model,
        langfuse=langfuse,
    )

    with st.sidebar:
        st.divider()
        if st.button("保存当前配置", use_container_width=True):
            save_ui_config(current_ui_config)
            st.success("已保存，下次启动会自动带出。")
        if st.button("清除已保存配置", use_container_width=True):
            clear_ui_config()
            st.success("已清除本地记忆。")
            st.rerun()
        st.caption(f"配置文件：`{UI_CONFIG_FILE}`")

    if section == "数据标准化":
        _render_standardization_page(
            dataset=dataset,
            output_dir=output_dir,
            agent_model=agent_model,
            enable_langfuse=enable_langfuse,
            langfuse=langfuse,
        )

    elif section == "表单质检":
        if st.button("开始质检", type="primary", key="run_form_quality"):
            save_ui_config(current_ui_config)
            config = _quality_config(
                dataset_path=Path(dataset),
                user_instruction="",
                mode=mode,
                samples=int(samples),
                output_dir=output_dir,
                dry_run=dry_run,
                enable_llm_analysis=enable_llm_analysis,
                enable_langfuse=enable_langfuse,
                solver_model=solver_model,
                agent_model=agent_model,
                langfuse=langfuse,
            )
            live_events, on_event = _create_live_event_recorder("实时运行记录", "quality_live_events")
            with st.spinner("质检智能体运行中..."):
                state = run_quality_agent(config, prefer_langgraph=use_langgraph, on_event=on_event)
            state["live_events"] = list(live_events)
            st.session_state["quality_state"] = state

        _render_quality_state(st.session_state.get("quality_state"))

    elif section == "Agent 会话":
        st.subheader("对话式 Agent")
        uploaded_file = st.file_uploader("上传数据集 JSON / JSONL", type=["json", "jsonl"], key="chat_dataset_json")

        if "agent_chat_messages" not in st.session_state:
            st.session_state["agent_chat_messages"] = [
                {
                    "role": "assistant",
                    "content": "请上传 JSON 或使用左侧数据集路径，然后直接输入质检要求。",
                }
            ]

        if st.button("清空会话", key="clear_agent_chat"):
            st.session_state["agent_chat_messages"] = []
            st.session_state.pop("chat_agent_result", None)
            st.session_state.pop("agent_chat_memory_summary", None)
            st.rerun()

        try:
            current_chat_dataset = _chat_dataset_path(uploaded_file, dataset, output_dir) if uploaded_file else Path(dataset)
        except Exception as exc:
            st.error(f"上传文件不是合法 JSON/JSONL：{exc}")
            current_chat_dataset = Path(dataset)
        st.caption(f"当前数据集：`{current_chat_dataset}`")

        for message in st.session_state.get("agent_chat_messages", []):
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        prompt = st.chat_input("输入质检要求，例如：检查这份 JSON 的难度并给出复核建议")
        if prompt:
            save_ui_config(current_ui_config)
            try:
                dataset_path = _chat_dataset_path(uploaded_file, dataset, output_dir)
                config = _quality_config(
                    dataset_path=dataset_path,
                    user_instruction=prompt,
                    mode=mode,
                    samples=int(samples),
                    output_dir=output_dir,
                    dry_run=dry_run,
                    enable_llm_analysis=enable_llm_analysis,
                    enable_langfuse=enable_langfuse,
                    solver_model=solver_model,
                    agent_model=agent_model,
                    langfuse=langfuse,
                )
                st.session_state["agent_chat_messages"].append({"role": "user", "content": prompt})
                with st.chat_message("assistant"):
                    live_events, on_event = _create_live_event_recorder("Agent 实时运行记录", "chat_live_events")
                    with st.spinner("Agent 正在理解需求..."):
                        response = run_chat_quality_agent(
                            user_message=prompt,
                            config=config,
                            chat_history=st.session_state.get("agent_chat_messages", []),
                            previous_summary=st.session_state.get("agent_chat_memory_summary"),
                            prefer_langgraph=use_langgraph,
                            on_event=on_event,
                        )
                    response["live_events"] = list(live_events)
                    response.setdefault("summary", {})["live_events"] = list(live_events)
                    st.markdown(response["reply"])
                st.session_state["agent_chat_messages"].append({"role": "assistant", "content": response["reply"]})
                st.session_state["chat_agent_result"] = response
                if response.get("tool_was_run") and response.get("summary"):
                    st.session_state["agent_chat_memory_summary"] = response["summary"]
            except json.JSONDecodeError as exc:
                st.error(f"上传文件不是合法 JSON：{exc}")
            except Exception as exc:
                st.error(f"Agent 执行失败：{exc}")

        chat_result = st.session_state.get("chat_agent_result")
        if chat_result and chat_result.get("tool_was_run"):
            st.subheader("Agent 工具执行摘要")
            _render_chat_process(chat_result.get("summary", {}))
            _render_collapsed_json("原始摘要数据（调试）", chat_result.get("summary", {}))
            if chat_result.get("used_react_agent"):
                st.caption("本次优先使用 LangGraph ReAct Agent 调用工具。")
            else:
                st.caption("本次使用固定 LangGraph 工作流兜底执行。")
        elif chat_result:
            st.caption("本轮为普通对话，未调用质检工具。")

    elif section == "报告结果":
        st.subheader("最近一次表单质检结果")
        _render_quality_state(st.session_state.get("quality_state"))

        chat_result = st.session_state.get("chat_agent_result")
        if chat_result:
            st.subheader("最近一次 Agent 会话结果")
            _render_chat_process(chat_result.get("summary", {}))
            _render_collapsed_json("原始摘要数据（调试）", chat_result.get("summary", {}))
        else:
            st.info("暂无 Agent 会话结果。")

    elif section == "模型配置":
        st.info("当前模型配置可通过左侧“保存当前配置”写入本机记忆。")


if __name__ == "__main__":
    main()
