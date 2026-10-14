# 开发

先用工程架构找到改动落点，再读所属专题与 [AGENTS.md](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/AGENTS.md)。Python 使用 uv，前端使用 pnpm；检查命令统一在 [README](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/README.md#开发与验证)。

| 内容 | 阅读入口 |
| --- | --- |
| 环境约束与依赖恢复 | [Python 清单](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/pyproject.toml)、[uv.lock](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/uv.lock)、[前端清单](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/web/package.json)、[前端锁文件](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/web/pnpm-lock.yaml) |
| 改动落点、生产/诊断边界 | [工程架构](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/engineering-architecture.md) |
| 文档分层、长度、事实证据与链接检查 | [文档治理](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/documentation-governance.md) |
| 决策背景、放弃方案和限制 | [笔记规则](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/.agents/notes/README.md) |
| 自动检查与实际版本 | [CI 工作流](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/.github/workflows/ci.yml) |
| 固定测试图片与模型来源 | [夹具说明](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/tests/fixtures/README.md)、[模型说明](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/models/README.md) |
| Wiki 源文件、检查与同步 | [Wiki 发布](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/wiki-publication.md) |

按 [开发与验证交接](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/project-handover.md#8-开发与验证交接)选择真实存在的检查。静态、模拟、集成和目标平台实测分别报告；回归在隔离副本运行，不使用运行实例数据，不覆盖其他贡献者改动。

[返回首页](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/wiki/Home.md)
