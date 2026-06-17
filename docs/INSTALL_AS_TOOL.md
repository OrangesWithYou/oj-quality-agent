# Install as a Command Line Tool

This project can be installed as an editable Python package so the CLIs can be
called with `oj-eval`, `oj-quality-agent`, and `oj-quality-ui` from the current
environment.

## Editable install for local development

```powershell
python -m pip install -e .
```

Then run:

```powershell
oj-eval --help
oj-quality-agent --help
oj-quality-ui
```

Equivalent module entry:

```powershell
python -m oj_eval --help
```

## Common commands

Run code-question evaluation:

```powershell
oj-eval --domain code --cases-file "cases/code/cases_to_test.json" --samples 4
```

Run objective-question evaluation:

```powershell
oj-eval --math --math-cases-file "cases/math/cases_math_small.json" --math-samples 4
```

Override model endpoint:

```powershell
oj-eval `
  --math `
  --math-cases-file "cases/math/cases_math_small.json" `
  --math-samples 4 `
  --ppio-base-url "https://your-openai-compatible-endpoint/v1" `
  --ppio-api-key "your-api-key" `
  --model "your-model-name"
```

## Build a wheel

```powershell
python -m pip wheel --no-deps -w dist .
```

Install the built wheel:

```powershell
python -m pip install dist/oj_quality_platform-0.1.0-py3-none-any.whl
```
