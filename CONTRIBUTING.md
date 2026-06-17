# Contributing

感谢你考虑参与 质检难题智能体_鲍元吉_20260617。这个项目目前以本地 Demo / 原型平台为主，欢迎围绕数据标准化、Agent 编排、评测工具链和可视化体验提交改进。

## 开发环境

推荐使用项目内虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[agent]"
```

Windows 用户也可以直接运行：

```bat
setup_quality_ui.bat
```

## 提交流程

1. Fork 仓库。
2. 创建分支：`git checkout -b feature/your-change`。
3. 保持改动聚焦，避免把格式化、重构和功能变更混在一个 PR。
4. 本地完成验证。
5. 提交 Pull Request，并说明改动范围、验证方式和风险。

## 本地验证

提交前至少运行：

```powershell
python -m compileall -q agents apps tools run_cases_file.py judge.py
python -B -c "import streamlit, langchain, langgraph, langfuse, quality_agent, quality_ui"
```

Windows 一键脚本检查：

```powershell
.\start_quality_ui.bat --check
.\setup_quality_ui.bat --check
```

## 注意事项

- 不要提交 `.venv/`、`runs/`、`reports/`、`__pycache__/`。
- 不要提交真实 API Key、Langfuse Key、Judge0 私有地址或 `.env` 文件。
- 涉及真实模型调用的验证，请在 PR 描述中说明是否会产生 API 成本。
- 新增字段标准化逻辑时，请同时考虑 JSON 和 JSONL。
- 新增 UI 功能时，请尽量保持中文界面和现有业务导航一致。
