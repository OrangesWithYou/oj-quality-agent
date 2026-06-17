# Security Policy

## Supported Versions

当前项目处于 `0.1.x` 原型阶段。安全修复优先面向主分支和最新发布版本。

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |

## Reporting a Vulnerability

如果你发现安全问题，请不要在公开 issue 中直接披露可利用细节。

推荐流程：

1. 使用 GitHub Security Advisories 私下报告。
2. 如果仓库尚未启用 Security Advisories，请先联系维护者建立私密沟通渠道。
3. 报告中请包含影响范围、复现步骤、相关日志和建议修复方向。

## Sensitive Data

本项目会接触模型 API Key、Langfuse Key、题库数据和模型调用轨迹。使用时请注意：

- 不要把真实密钥提交到仓库。
- 不要把包含敏感题库或业务数据的 `runs/`、`reports/` 上传到公开仓库。
- 公开 issue 或 PR 中不要贴出真实 API 响应、用户数据或私有题目内容。
