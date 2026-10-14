# 文档总览

按你要完成的事情选择入口，不必逐篇读完。第一次运行先看 [项目首页](../README.md)；已经能运行、想改代码时，直接看 [架构概述](../DESIGN.md)和工程边界。

## 先运行起来

| 目标 | 从这里开始 |
| --- | --- |
| 判断项目适不适合自己，找源码、样例和发行入口 | [公开项目说明](public-project.md) |
| 从源码启动工作台与 API | [README：本地启动](../README.md#本地启动) |
| 部署 CPU/ARM64 或 NVIDIA 容器 | [Docker 部署](docker-deployment.md) |
| 使用 Windows 完整包、修复包或 GPU 升级包 | [Windows 便携版](windows-portable.md) |
| 准备模型与检查下载完整性 | [模型资产](../models/README.md) |
| 配置可选视觉分析与图像编辑服务 | [AI 模型目录](model-catalog.md) |

先完成本地换脸，再配置可选 AI，能更容易区分模型资产、运行环境和上游服务的问题。源码启动、CPU 容器与 Windows 包的依赖流程不同，请按自己的安装方式阅读。

## 使用、保存与恢复

| 你遇到的操作 | 对应专题 |
| --- | --- |
| 准备人物参考，导入或归档素材 | [素材库](portrait-library.md) |
| 理解人物匹配、保护画笔、遮挡和换脸结果 | [本地换脸](facefusion-local-swap.md) |
| 查看批任务进度、逐图结果、历史与删除 | [任务历史](task-history.md) |
| 自动保存、刷新、从旧记录恢复编辑 | [编辑工程](studio-projects.md) |
| 在界面确认诊断、生成、接受与导出 | [精修工作台](refinement-workbench.md) |
| 区分精修会话、轮次、图片版本与接受状态 | [精修契约](ai-refinement.md) |
| 理解原图、当前图和身份参考各自的作用 | [精修分析](refinement-analysis.md) |
| 理解整图生成、复评、失败与重试 | [精修生成](refinement-generation.md) |
| 对接 HTTP、SSE、图片读取与会话存储 | [精修接口](refinement-session-api.md) |
| 核对提示词版本与依据 | [提示词说明](refinement-prompts.md) |
| 查看旧任务人工评价与证据档案 | [质量档案](delivery-quality.md) |

### 容易混淆的词

- **参考与目标**：参考提供要换入的人物身份；目标是要处理的照片。人物绑定规则见素材库与本地换脸专题。
- **工程与任务**：工程保存编辑工作区；任务记录服务端的一次执行。打开历史不等于恢复工程，详见编辑工程。
- **会话、轮次与版本**：会话组织一次精修的完整历史；轮次是一次命令；版本是保留的图片。字段与状态以精修契约为准。
- **查看、继续编辑与接受**：看到一张候选不等于接受它；继续编辑的基图与最终接受版也可不同，详见精修工作台。

## 部署、排错与交接

| 你要解决的问题 | 对应专题 |
| --- | --- |
| 登记实例、健康检查、备份、恢复、升级与排错 | [运行维护手册](operations-runbook.md) |
| 接任一个项目或确认端到端交付范围 | [综合交接说明](project-handover.md) |
| Mac/Colima 启动守卫、安装及检查限制 | [Mac 守卫](macos-boot-guard.md) |
| 大图内存、排队、取消收尾与实际模型后端 | [内存与排队](memory-scheduling.md) |
| 运行离线比较，判断实测结论能否适用 | [评测说明](swap-benchmarks.md) |
| 遇到凭据、个人图片或文件访问暴露 | [私下报告](../SECURITY.md) |

排错时先确定实例、安装方式和版本，再使用对应指南。健康就绪、配置就绪、真实上游调用与画质验收是不同结论；测试或历史记录只证明它们明确覆盖的范围。

## 开发与文档维护

| 目标 | 入口 |
| --- | --- |
| 了解模块与数据所有权 | [架构概述](../DESIGN.md) |
| 判断改动放在哪个模块 | [工程架构](engineering-architecture.md) |
| 查权威文档、写作规则与机械检查 | [文档治理](documentation-governance.md) |
| 提交问题、改代码或准备 PR | [贡献说明](../CONTRIBUTING.md)、[Agent 规则](../AGENTS.md) |
| 维护版本化 Wiki 源稿与独立站点 | [Wiki 发布说明](wiki-publication.md)、[Wiki 源码首页](../wiki/Home.md) |
| 了解方案选择的原因 | [决策笔记规则](../.agents/notes/README.md)，按主题和文件名检索 |
| 使用仓库集成测试图片 | [测试夹具说明](../tests/fixtures/README.md) |

## 证据与历史怎么看

当前行为以专题文档及其链接的代码、配置和测试为准。[Changelog](../CHANGELOG.md)记录变化时间，决策笔记解释原因；两者不替代当前契约。

带日期的实测和 `validation/` 记录保留当时的输入、版本、环境与限制。它们不是最新能力清单，也不随可读性改写刷新结果。证据入口集中在 [评测说明](swap-benchmarks.md)、[综合交接说明](project-handover.md)和 [运行维护手册](operations-runbook.md)。
