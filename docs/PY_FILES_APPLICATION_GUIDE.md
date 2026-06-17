# Python 文件应用层说明（全量版）

> 目标：只讲“每个 `.py` 文件在业务流程里做什么、你该怎么用”，不展开底层实现细节。

---

## 1. 当前项目 Python 文件清单

当前项目已经整理为单仓多模块结构。本文件主要说明 OJ 评测工具链，
真实源码位于 `tools/oj_eval/` 下；根目录的 `run_cases_file.py` 仅作为旧命令兼容入口。

OJ 工具链的核心 Python 文件：

1. `run_cases_file.py`
2. `tools/oj_eval/oj_eval/cli.py`
3. `tools/oj_eval/pipeline_app/__init__.py`
4. `tools/oj_eval/pipeline_app/cli.py`
5. `tools/oj_eval/pipeline_app/application.py`
6. `tools/oj_eval/pipeline_app/case_service.py`
7. `tools/oj_eval/pipeline_app/constants.py`
8. `tools/oj_eval/pipeline_core/`
9. `judge.py`

---

## 2. 先看整体调用关系（应用层视角）

典型运行链路：

`run_cases_file.py`（兼容入口）
→ `tools/oj_eval/pipeline_app/cli.py`（参数解析）
→ `tools/oj_eval/pipeline_app/application.py`（业务编排）
→ `tools/oj_eval/pipeline_app/case_service.py`（题目数据准备）
→ `tools/oj_eval/pipeline_core/`（生成 + 判题 + 汇总）

`judge.py` 是独立排障脚本，不在主链路里。

---

## 3. 每个 py 文件是干什么的、如何使用

### 3.1 `run_cases_file.py`（兼容入口）

**作用**
- 保留历史执行方式，避免老命令失效。
- 把执行请求转发到新的模块化 CLI。

**怎么用**
- 日常首选入口：
  - `python run_cases_file.py --help`
  - `python run_cases_file.py --cases-file cases_to_test.json`

**什么时候改它**
- 通常不需要改。除非你要调整“兼容入口”的行为或导出接口。

---

### 3.2 `tools/oj_eval/pipeline_app/__init__.py`（应用层包声明）

**作用**
- 标记 `pipeline_app` 是一个可导入的 Python 包。
- 提供模块级说明（入口建议、用途提示）。

**怎么用**
- 一般不直接运行。
- 通过 `python -m pipeline_app.cli ...` 触发该包下 CLI。

**什么时候改它**
- 想补充包级文档，或统一管理包导出符号时。

---

### 3.3 `tools/oj_eval/pipeline_app/cli.py`（命令行接口层）

**作用**
- 统一定义所有命令参数（domain、cases、采样参数、接口地址等）。
- 负责“解析参数 + 调用应用编排层 + 打印 JSON 结果”。

**怎么用**
- 直接模块运行：
  - `python -m pipeline_app.cli --help`
- 或通过兼容入口间接使用（更推荐给日常使用者）。

**什么时候改它**
- 新增命令参数、修改参数默认值、优化命令行体验。

---

### 3.4 `tools/oj_eval/pipeline_app/application.py`（应用编排层）

**作用**
- 组织完整业务流程：参数 → 题目来源 → 运行器执行 → 结果摘要。
- 处理多种模式：
  - `--init-domain-layout`（初始化目录）
  - `--export-first-from-excel`（只导出，不评测）
  - 常规评测模式（完整跑批）

**怎么用**
- 你不直接调用它的命令；它由 CLI 自动调用。
- 你在命令行看到的“模式行为”基本由这里决定。

**什么时候改它**
- 需要新增业务流程（比如“只生成不判题”、“只复跑 hard 题”）时。

---

### 3.5 `tools/oj_eval/pipeline_app/case_service.py`（题目数据服务层）

**作用**
- 统一管理题目从哪里来、如何转为标准结构：
  - JSON 读取
  - demo 题库初始化/重建
  - Excel 首题导出到 JSON

**怎么用**
- 通过命令参数触发：
  - `--use-demo-cases`
  - `--regen-cases`
  - `--export-first-from-excel <excel_path>`

**什么时候改它**
- 需要接新题库格式、加字段兼容、改数据清洗规则时。

---

### 3.6 `tools/oj_eval/pipeline_app/constants.py`（配置常量层）

**作用**
- 维护默认路径与 `domain` 预设（`general/code/math`）。
- 决定“不显式传参时”系统会用哪些默认目录。

**怎么用**
- 正常运行不需要直接操作。
- 想改默认目录时，改这里最集中。

**什么时候改它**
- 你要统一调整默认 `cases` 路径、`runs` 输出路径、run 前缀。

---

### 3.7 `pipeline_core.py`（核心能力层）

**作用**
- 提供真正执行评测所需的底层能力：
  - PPIO 生成调用
  - Judge0 判题调用
  - 采样计划处理
  - 单题/整批运行与汇总文件输出

**怎么用**
- 主流程会自动调用。
- 你做二次开发时，也可以把这里当“能力库”复用。

**什么时候改它**
- 调整模型请求策略、判题策略、汇总结构、日志/落盘结构时。

---

### 3.8 `judge.py`（Judge0 快速排障脚本）

**作用**
- 用最小样例验证 Judge0 通路是否可用（提交 + 轮询 + 打印结果）。

**怎么用**
- 直接运行：
  - `python judge.py`

**什么时候用它**
- 主流程判题异常时，先快速判断是 Judge0 网络/服务问题，还是主流程配置问题。

---

## 4. 常用应用场景命令（建议收藏）

### 4.1 常规跑批

```bash
python run_cases_file.py --cases-file cases_to_test.json --output-dir runs/success_cases
```

### 4.2 按业务域隔离运行

```bash
python run_cases_file.py --domain code
python run_cases_file.py --domain math
```

### 4.3 一次性初始化 code/math 目录

```bash
python run_cases_file.py --init-domain-layout
```

### 4.4 只从 Excel 导出首题到 cases（不跑评测）

```bash
python run_cases_file.py --export-first-from-excel "OJ测试7-3(1).xlsx"
```

### 4.5 Judge0 连通性排障

```bash
python judge.py
```

---

## 5. 运行前准备（应用层）

- Python 3 环境
- 依赖：`requests`（如果用 Excel 功能，再安装 `openpyxl`）
- 建议配置环境变量：
  - `PPIO_API_KEY`
  - `PPIO_BASE_URL`
  - `PPIO_MODEL`
  - `JUDGE0_BASE_URL`
  - `JUDGE0_LANGUAGE_ID`
