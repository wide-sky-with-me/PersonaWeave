# 架构

整体代码地图及两条处理链见 [综合交接说明](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/project-handover.md#4-代码地图与改动落点)。本页只索引各领域的权威事实源。

| 领域 | 说明及可执行来源 |
| --- | --- |
| 系统与依赖方向 | [DESIGN](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/DESIGN.md)、[工程架构](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/engineering-architecture.md) |
| API 装配与生命周期 | [app.py](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/api/app.py)、[lifecycle.py](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/api/lifecycle.py) |
| 本地换脸、人物分配和掩码 | [本地换脸契约](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/facefusion-local-swap.md) |
| AI 模型目录、网关和结构化输出 | [模型目录](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/model-catalog.md)、[网关配置](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/config/ai_gateway.yaml) |
| session / turn / version | [精修契约](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/ai-refinement.md)、[接口与存储](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/refinement-session-api.md) |
| 工程、历史及素材数据所有权 | [工程](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/studio-projects.md)、[任务历史](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/task-history.md)、[素材库](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/portrait-library.md) |
| 内存、队列与原生推理生命周期 | [内存与排队](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/memory-scheduling.md) |

请求字段以 [通用 schema](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/api/schemas.py)和 [精修 HTTP 契约](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/api/refinement/contracts.py)为准。选择原因按主题查 [决策笔记规则](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/.agents/notes/README.md)，变更时间查 [Changelog](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/CHANGELOG.md)。

[返回首页](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/wiki/Home.md)
