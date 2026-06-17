# 质检难题智能体_鲍元吉_20260617

> 面向题库采购与数据集交付场景的智能质检平台：支持 JSON / JSONL 字段标准化、OJ/客观题评测、LangGraph Agent 编排、LangChain 工具封装、Langfuse 轨迹追踪与 Streamlit 可视化操作。

[![Python](https://img.shields.io/badge/Python-%3E%3D3.10-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-ff4b4b)](https://streamlit.io/)
[![LangChain](https://img.shields.io/badge/Agent-LangChain-1c3c3c)](https://www.langchain.com/)
[![LangGraph](https://img.shields.io/badge/Workflow-LangGraph-1c3c3c)](https://www.langchain.com/langgraph)
[![Langfuse](https://img.shields.io/badge/Tracing-Langfuse-orange)](https://langfuse.com/)

## 目录

- [项目简介](#项目简介)
- [核心功能](#核心功能)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [使用方式](#使用方式)
- [数据格式](#数据格式)
- [输出产物](#输出产物)
- [配置说明](#配置说明)
- [开发与验证](#开发与验证)
- [发布到 GitHub 前检查](#发布到-github-前检查)
- [贡献方式](#贡献方式)
- [许可证](#许可证)
- [致谢](#致谢)

## 项目简介

OJ Quality Platform 是一个本地运行的数据集质检 Agent Demo / 平台。它把原有 `oj-eval` 评测工具包装成可被 Agent 调用的工具，再通过 LangGraph 编排完整质检流程，并提供 Streamlit 中文可视化界面。

适用场景：

- 采购或验收题库数据集时，快速检查字段完整性、题目类型和潜在坏题。
- 把不同来源的 JSON / JSONL 题库统一转换为标准试题数据模型。
- 对编程题和客观题进行模型采样评测，形成可复核报告。
- 记录 Agent 与底层评测模型调用轨迹，便于用 Langfuse 回溯。

## 核心功能

- **字段标准化**：支持 JSON / JSONL 输入，输出标准 JSON 或 JSONL。
- **字段映射**：支持规则字段映射、人工确认映射、映射模板复用。
- **LLM 辅助映射**：可让 Agent 模型根据样例字段猜测映射关系。
- **完整性质检**：在调用大模型前先做低成本结构检查。
- **OJ 评测工具链**：支持编程题代码生成、Judge0 判题、客观题自动判分。
- **Agent 编排**：使用 LangGraph 描述质检流程，未安装时有顺序执行兜底。
- **LangChain 工具封装**：将 `oj-eval` 封装为 `StructuredTool` 供 Agent 调用。
- **Langfuse 追踪**：Agent 模型调用和底层评测模型调用均可上传轨迹。
- **可视化界面**：Streamlit 中文界面，支持模型配置、数据上传、实时日志、报告查看。
- **一键启动**：Windows 下提供 `setup_quality_ui.bat` 和 `start_quality_ui.bat`。

## 技术栈

| 类型 | 技术 |
| --- | --- |
| 语言 | Python >= 3.10 |
| UI | Streamlit |
| Agent 编排 | LangGraph |
| 模型与工具封装 | LangChain / langchain-openai |
| 轨迹追踪 | Langfuse |
| 评测工具 | 自研 `oj-eval` |
| 编程题判题 | Judge0 |
| 包管理 | `pyproject.toml` / editable install |

## 项目结构

```text
oj-eval/
├── agents/
│   └── quality_agent/          # Agent 编排层
│       └── quality_agent/
│           ├── core/           # 配置与状态对象
│           ├── integrations/   # Langfuse 集成
│           ├── nodes/          # 检查、评测、评分、分析、报告节点
│           ├── standardization/# 字段标准化、映射模板、LLM 字段猜测
│           ├── tools/          # LangChain Tool 适配器
│           └── workflow/       # LangGraph 工作流与顺序兜底
├── apps/
│   └── quality_ui/             # Streamlit 可视化界面
├── tools/
│   └── oj_eval/                # 底层 OJ / 客观题评测工具
│       ├── oj_eval/            # `oj-eval` 命令入口
│       ├── pipeline_app/       # CLI、题库加载、模式分发
│       └── pipeline_core/      # 模型调用、Judge0、报告汇总
├── cases/                      # 示例题库
├── docs/                       # 设计、安装和验收文档
├── run_cases_file.py           # 兼容旧命令入口
├── judge.py                    # Judge0 连通性排查脚本
├── setup_quality_ui.bat        # 初始化本项目虚拟环境
├── start_quality_ui.bat        # 启动可视化界面
└── pyproject.toml
```

## 快速开始

### Windows 一键启动

首次使用：

```bat
setup_quality_ui.bat
```

日常启动：

```bat
start_quality_ui.bat
```

启动后访问：

```text
http://localhost:8501
```

### 手动安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[agent]"
```

启动 UI：

```powershell
python -m streamlit run apps/quality_ui/quality_ui/app.py
```

也可以使用安装后的命令：

```powershell
oj-quality-ui
```

## 使用方式

### 1. 数据标准化

在 UI 中进入“数据标准化”页面：

1. 上传原始 JSON / JSONL。
2. 点击读取并生成规则映射建议。
3. 可选：调用大模型辅助猜测字段映射。
4. 人工确认字段映射关系。
5. 选择输出格式：JSON 或 JSONL。
6. 生成标准化文件。
7. 将 `quality_dataset` 设置为质检数据集。

标准化会生成：

```text
standard_questions.json 或 standard_questions.jsonl
quality_dataset.json 或 quality_dataset.jsonl
field_mapping.json
normalization_report.json
```

### 2. 表单质检

在 UI 中进入“表单质检”页面：

- 选择题库路径或使用标准化后的 `quality_dataset`。
- 选择题库类型：自动识别、编程题、客观题、混合题库。
- 设置每题采样次数。
- 配置评测模型和 Agent 模型。
- 可开启 Langfuse 轨迹记录。
- 点击开始质检。

Dry run 模式只做结构检查和流程验证，不调用评测模型。

### 3. Agent 会话

在 UI 中进入“Agent 会话”页面：

- 上传 JSON / JSONL 数据集。
- 输入自然语言质检要求。
- Agent 会根据用户意图调用相关工具并返回结果。

### 4. CLI 使用

Agent dry-run：

```powershell
oj-quality-agent run --dataset "cases/math/cases_math_small.json" --dry-run
```

编程题评测：

```powershell
oj-eval --domain code --cases-file "cases/code/cases_to_test.json" --samples 4
```

客观题评测：

```powershell
oj-eval --math --math-cases-file "cases/math/cases_math_small.json" --math-samples 4
```

覆盖 OpenAI 兼容模型接口：

```powershell
oj-eval `
  --math `
  --math-cases-file "cases/math/cases_math_small.json" `
  --math-samples 4 `
  --ppio-base-url "https://your-openai-compatible-endpoint/v1" `
  --ppio-api-key "your-api-key" `
  --model "your-model-name"
```

## 数据格式

### 标准试题字段

| 字段 | 说明 | 是否必须存在 |
| --- | --- | --- |
| `id` | 试题唯一标识 | 是 |
| `type` | 题目类型，如 `single_choice`、`multiple_choice`、`judge_choice`、`programming` | 是 |
| `question` | 题干内容 | 是 |
| `options` | 选项数组 | 是 |
| `answer` | 正确答案 | 是 |
| `analysis` | 答案解析 | 是 |
| `knowledge_points` | 知识点数组 | 是 |
| `language` | 语言，如 `zh` | 是 |
| `difficulty` | 难易程度 | 是 |
| `subject` | 学科 | 推荐 |
| `grade` | 年级或学段 | 推荐 |
| `is_contest` | 是否竞赛题 | 推荐 |
| `contest_type` | 赛事类型 | 推荐 |
| `source` | 题目来源 | 推荐 |

说明：字段必须存在，但字段值可以根据实际业务情况为空。

### JSON 示例

```json
{
  "questions": [
    {
      "id": "q001",
      "type": "single_choice",
      "question": "1 + 1 = ?",
      "options": [
        {"label": "A", "text": "1"},
        {"label": "B", "text": "2"}
      ],
      "answer": "B",
      "analysis": "",
      "knowledge_points": [],
      "language": "zh",
      "difficulty": "易",
      "subject": "数学",
      "grade": "",
      "is_contest": false,
      "contest_type": "",
      "source": ""
    }
  ]
}
```

### JSONL 示例

```jsonl
{"id":"q001","type":"single_choice","question":"1 + 1 = ?","options":[{"label":"A","text":"1"},{"label":"B","text":"2"}],"answer":"B","analysis":"","knowledge_points":[],"language":"zh","difficulty":"易","subject":"数学","grade":"","is_contest":false,"contest_type":"","source":""}
{"id":"q002","type":"programming","question":"Read two integers and output their sum.","options":[],"answer":"3","analysis":"","knowledge_points":[],"language":"en","difficulty":"易","subject":"","grade":"","is_contest":false,"contest_type":"","source":""}
```

## 输出产物

一次质检运行会在 `runs/quality_agent/run_quality_YYYYMMDD_HHMMSS_ffffff/` 下生成：

```text
quality_report.md          # 给人看的质检报告
quality_summary.json       # 给程序读取的结构化结果
completeness_findings.json # 完整性检查问题
review_queue.json          # 建议人工复核的题目
trace_metadata.json        # 运行轨迹元数据
```

底层 `oj-eval` 还会生成模型请求、响应、判题结果、采样摘要等文件。`runs/` 是运行产物，不建议提交到 GitHub。

## 配置说明

### 模型配置

UI 中有两个模型槽位：

- **评测模型**：用于底层 `oj-eval` 做题、生成代码或回答客观题。
- **Agent 模型**：用于字段映射猜测、归因分析和复核建议。

均支持 OpenAI 兼容接口：

```text
Base URL
API Key
Model Name
Temperature
Top P
Max Tokens
```

### 环境变量

```powershell
$env:PPIO_API_KEY = "..."
$env:PPIO_BASE_URL = "https://your-openai-compatible-endpoint/v1"
$env:PPIO_MODEL = "your-model-name"

$env:JUDGE0_BASE_URL = "https://ce.judge0.com"
$env:JUDGE0_LANGUAGE_ID = "71"

$env:LANGFUSE_PUBLIC_KEY = "..."
$env:LANGFUSE_SECRET_KEY = "..."
$env:LANGFUSE_BASE_URL = "https://cloud.langfuse.com"
```

### Langfuse

开启 Langfuse 后，系统会尽量把所有大模型调用轨迹上传到 Langfuse，包括：

- Agent 模型调用。
- 字段映射 LLM 猜测。
- LLM 归因分析。
- 底层 `oj_eval` 中的评测模型调用。

## 开发与验证

启动脚本检查：

```powershell
.\start_quality_ui.bat --check
.\setup_quality_ui.bat --check
```

Python 导入检查：

```powershell
python -B -c "import streamlit, langchain, langgraph, langfuse, quality_agent, quality_ui"
```

编译检查：

```powershell
python -m compileall -q agents apps tools run_cases_file.py judge.py
```

CLI 帮助：

```powershell
oj-eval --help
oj-quality-agent --help
```

## 发布到 GitHub 前检查

不要提交：

```text
.venv/
runs/
reports/
__pycache__/
*.egg-info/
.env
.streamlit/quality_ui_config.json
.streamlit/field_mapping_templates.json
```

建议补充：

```text
screenshots/
docs/quickstart.md
```

当前仓库已经包含 MIT 许可证。正式发布前建议再补充 UI 截图和更细的 quickstart 文档。

## 贡献方式

如果这个项目后续作为公开仓库维护，建议采用常规 Pull Request 流程：

1. Fork 仓库。
2. 创建功能分支。
3. 提交修改。
4. 补充必要的测试或验证说明。
5. 发起 Pull Request。

当前项目仍处于 Demo / 原型阶段，建议优先完善测试、示例数据和截图后再正式开放协作。

## 许可证

本项目使用 MIT License。详情见 [LICENSE](LICENSE)。

## 相关文档

- [Agent 设计说明](docs/QUALITY_AGENT_DESIGN.md)
- [安装为命令行工具](docs/INSTALL_AS_TOOL.md)
- [Python 文件应用指南](docs/PY_FILES_APPLICATION_GUIDE.md)
- [Agent 验收报告](docs/AGENT_ACCEPTANCE_REPORT.md)
- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)
- [行为准则](CODE_OF_CONDUCT.md)

## 致谢

本 README 结构参考了 [othneildrew/Best-README-Template](https://github.com/othneildrew/Best-README-Template)，并根据当前项目实际功能进行了裁剪。
