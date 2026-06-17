# 难题质检 Agent 验收报告

验收时间：2026-06-13  
验收范围：工具层 `oj-eval`、Agent 编排层 `quality_agent`、可视化层 `quality_ui`、启动脚本、外部模型/Judge0 最小真实链路。

## 结论

当前系统达到“小 demo / 内部演示 / 小规模质检验证”验收标准：

- math / code / mixed 三类数据集的结构识别、dry-run 质检、报告落盘可用。
- LangGraph 工作流和顺序兜底路径可用。
- Agent 会话已支持普通对话、意图识别、多轮追问、重新执行。
- Streamlit UI 四个业务区可正常渲染，Langfuse 配置默认收起，长 JSON 默认收进调试折叠框。
- 真实数学题 solver 调用通过，真实简单代码题 solver + Judge0 链路通过。

有条件项：

- 复杂代码题真实评测链路可运行，但当前配置模型在复杂题上返回 `reasoning_content` 且 `content` 为空，导致样本 `NOT_RUN`。这属于模型/参数适配风险，不是 Judge0 或流程崩溃。
- Langfuse 仅验证了未启用/未配置时的降级路径；因当前没有完整 Langfuse key，未做真实云端 trace 验证。
- 当前本机配置开启了明文保存密钥能力，应避免把 `.streamlit/quality_ui_config.json` 提交或外发。

## 验收明细

| 模块 | 验收项 | 结果 | 证据 |
| --- | --- | --- | --- |
| 基础环境 | Python 编译检查 | 通过 | `python -m compileall -q agents apps tools run_cases_file.py judge.py` |
| 启动脚本 | `start_quality_ui.bat --check` | 通过 | 导入 `streamlit / quality_agent / quality_ui` 成功 |
| Agent 工作流 | math dry-run | 通过 | `runs/agent_acceptance_backend_v3/math_dry/...` |
| Agent 工作流 | code dry-run | 通过 | `runs/agent_acceptance_backend_v3/code_dry/...` |
| Agent 工作流 | mixed dry-run | 通过 | `runs/agent_acceptance_backend_v3/mixed_dry/...` |
| Agent 工作流 | sequential fallback | 通过 | `runs/agent_acceptance_backend_v3/math_seq/...` |
| Agent 工作流 | LangGraph build | 通过 | `CompiledStateGraph` |
| Agent 会话 | 普通问候不跑工具 | 通过 | `你好 -> tool_was_run=False` |
| Agent 会话 | 明确质检触发工具 | 通过 | `检查这份数据集... -> tool_was_run=True` |
| Agent 会话 | 追问上一轮不重跑 | 通过 | `刚才结果怎么看 -> tool_was_run=False` |
| Agent 会话 | 重新执行 | 通过 | `重新跑一次 -> tool_was_run=True` |
| Langfuse | 未启用降级 | 通过 | callback 返回空列表，元数据生成正常 |
| UI 配置 | 本地记忆读写 | 通过 | `.streamlit/quality_ui_config.json` roundtrip |
| CLI | `quality_agent run --dry-run` | 通过 | 输出 JSON 摘要和 5 类报告路径 |
| CLI | `oj_eval --init-domain-layout` | 通过 | 初始化命令输出结构化 JSON |
| CLI | `oj_eval` dummy key 错误兜底 | 通过 | 输出 invalid sample summary，不崩溃 |
| UI | 首屏 / 四个导航 / Langfuse 收起 | 通过 | 浏览器 DOM 无 `StreamlitAPIException` |
| UI | Agent 普通对话 | 通过 | 不显示工具摘要，不展开长 JSON |
| UI | 报告页 / 表单页导航 | 通过 | 页面可切换，无异常 |
| 外部模型 | 真实数学题 1 题 1 样本 | 通过 | `valid_samples=1, invalid_samples=0` |
| Judge0 | 固定代码连通性 | 通过 | `Accepted` |
| 外部模型 + Judge0 | 简单代码题 1 题 1 样本 | 通过 | `valid_samples=1, passed_samples=1, Accepted` |
| 外部模型 + Judge0 | 复杂代码题 1 题 1 样本 | 有条件通过 | 链路不崩溃，但模型空 `content`，样本 `NOT_RUN` |

后端集中验收结果文件：

```text
runs/agent_acceptance_backend_v3/backend_acceptance_results.json
```

真实外部链路证据目录：

```text
runs/agent_acceptance_live_solver/run_quality_20260613_115219_381185
runs/agent_acceptance_live_code_easy/run_quality_20260613_115506_774767
runs/agent_acceptance_live_code/run_quality_20260613_115259_705183
```

## 本轮修复

1. 普通 Agent 对话分支增加短超时和本地兜底，避免外部模型慢或不可用时页面长时间卡住。
2. UI spinner 从“Agent 正在调用质检工具...”改为“Agent 正在理解需求...”，避免普通对话误导用户。

## 剩余风险和建议

1. 复杂代码题建议更换非推理型代码模型，或提高 `max_tokens`，避免模型只返回 `reasoning_content` 不返回 `content`。
2. 生产化前建议增加正式测试目录，例如 `tests/acceptance/`，把本次验收脚本沉淀为可重复运行的命令。
3. 若要演示 Langfuse，需要配置真实 `Public Key / Secret Key / Base URL` 后再跑一次 Agent 会话或 LLM 归因。
4. 当前密钥可被保存到本地明文配置文件，演示环境可接受，生产环境建议改为环境变量或本机密钥管理。
