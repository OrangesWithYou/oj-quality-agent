# Quality Agent Design

This document describes the current workflow-style dataset quality agent.

## Commands

Install local development entry points:

```powershell
python -m pip install -e .
```

Install optional agent/UI dependencies:

```powershell
python -m pip install -e .[agent]
```

Run the quality-agent CLI without expensive model calls:

```powershell
oj-quality-agent run --dataset "cases/math/cases_math_small.json" --dry-run
```

Launch the UI:

```powershell
oj-quality-ui
```

or:

```powershell
streamlit run apps/quality_ui/quality_ui/app.py
```

## Package structure

```text
tools/oj_eval/
  oj_eval/          # installed command wrapper for oj-eval
  pipeline_app/     # existing OJ evaluation application layer
  pipeline_core/    # existing solver/Judge0/evaluation core

agents/quality_agent/
  quality_agent/
    core/           # shared config and state
    nodes/          # inspection, evaluation, scoring, analysis, reporting nodes
    tools/          # LangChain Tool adapters, including oj_eval_tool
    workflow/       # LangGraph graph and sequential fallback
    integrations/   # Langfuse integration
    cli.py          # oj-quality-agent entry point

apps/quality_ui/
  quality_ui/
    app.py          # Streamlit UI
    launcher.py     # oj-quality-ui entry point
```

## Model roles

- Solver model: used by the existing `oj-eval` tool to answer objective questions
  or generate code for Judge0.
- Agent model: used by the quality agent to explain risky items and generate
  review suggestions.

Both models are configurable through CLI flags or the Streamlit UI.

Langfuse tracing can also be configured through the Streamlit UI:

- `Langfuse Public Key`
- `Langfuse Secret Key`
- `Langfuse Base URL`

If these fields are left empty, the agent falls back to `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`, and `LANGFUSE_HOST`.

## Workflow

```text
inspect_dataset
  -> finalize_trace_metadata
  -> run_eval [calls LangChain StructuredTool: oj_eval_tool]
  -> score_quality
  -> [if LLM analysis enabled and review candidates exist] llm_analysis
  -> write_report
```

When LangGraph is installed, the workflow runs through `StateGraph` with a
conditional edge after `score_quality`. Otherwise the same routing semantics run
through the sequential fallback.

## Tooling boundary

`oj-eval` remains a standalone evaluation tool under `tools/oj_eval`. The Agent
does not import its internals directly. Instead, `quality_agent.tools.oj_eval_tool`
wraps the CLI as a LangChain `StructuredTool`, and the LangGraph `run_eval` node
invokes that tool.

This keeps the boundary clear:

```text
LangGraph workflow
  -> LangChain StructuredTool: oj_eval_tool
  -> oj-eval CLI
  -> reportable evaluation evidence
```

The tool invocation metadata is written into `quality_summary.json` under
`eval_summary.tool_invocation`.

## Output files

Each run writes to `runs/quality_agent/run_quality_YYYYMMDD_HHMMSS/`:

- `quality_summary.json`
- `quality_report.md`
- `review_queue.json`
- `completeness_findings.json`
- `trace_metadata.json`

## Langfuse

Set these environment variables before enabling Langfuse:

```powershell
$env:LANGFUSE_PUBLIC_KEY = "..."
$env:LANGFUSE_SECRET_KEY = "..."
$env:LANGFUSE_BASE_URL = "https://cloud.langfuse.com"
```

The agent attaches Langfuse callbacks to LangChain LLM calls when optional
dependencies are installed and credentials are configured through UI, CLI flags,
or environment variables.
