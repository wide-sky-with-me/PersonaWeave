# 文档治理

文档是交付行为的一部分：改变了对外可见的流程、默认值、模型、状态、安全规则、存储规则或失败语义，却没有同时更新对应文档和回归证据，这次改动就是不完整的。本文件定义文档分层、权威来源、写作规则和机械检查；`AGENTS.md` 只保留规则和指向这里的链接。

## 分层

| 层 | 位置 | 回答的问题 | 写法 |
| --- | --- | --- | --- |
| 规则 | [AGENTS.md](../AGENTS.md) | Agent 开工必须遵守什么 | 每条一两行，细节用链接 |
| 事实 | `docs/*.md`、`README.md`、`DESIGN.md` | 现在的系统怎样运转 | 只写确定的当前事实 |
| 决策 | [.agents/notes/](../.agents/notes/README.md) | 当初为什么这样做、否定过什么 | 背景、决策、被放弃的方案、影响与验证 |
| 历史 | [CHANGELOG.md](../CHANGELOG.md) | 什么时候改了什么 | 按日期追加，不作为当前能力依据 |
| 实测记录 | `docs/*-validation.md`、`docs/refinement-live-test-*.md`、`docs/validation/` | 某次验证的输入、指标和校验和 | 带日期，说明适用范围，不外推 |

这套分层的取舍与被否定的方案见 [决策笔记](../.agents/notes/platform/20261004-three-layer-documentation.md)。事实层与决策层不能混写：事实文档里放历史取舍、踩坑过程和被舍弃的方案，会让人找一个功能点的描述都要翻很久；决策笔记里放当前接口细节则会很快过期。

## 权威层级

两处描述冲突时，以下表中最窄的权威来源为准，并在同一变更里修正另一处。`README.md` 是面向用户的能力摘要，`DESIGN.md` 说明架构，二者必须链接到权威文档，不得另建一套状态机或模型清单。

| Topic | Canonical current document | Executable fact source | Regression evidence |
| --- | --- | --- | --- |
| Conversational whole-image refinement | [ai-refinement.md](./ai-refinement.md); evidence/diagnosis in [refinement-analysis.md](./refinement-analysis.md); generation/review in [refinement-generation.md](./refinement-generation.md); routes/storage in [refinement-session-api.md](./refinement-session-api.md); UI in [refinement-workbench.md](./refinement-workbench.md) | [`service.py`](../api/refinement/service.py), [`store.py`](../api/refinement/store.py), [`artifacts.py`](../api/refinement/artifacts.py), [`contracts.py`](../api/refinement/contracts.py), [`conversation.py`](../core/refinement/conversation.py) | `tests/test_refinement_sessions.py`, `tests/test_refinement_artifacts.py`, `tests/test_refinement_conversation.py`, `tests/test_batch_refinement.py`, `tests/test_legacy_refinement.py` |
| Refinement evidence and prompt contracts | [refinement-analysis.md](./refinement-analysis.md) and [refinement-prompts.md](./refinement-prompts.md) (version and sources), subordinate to the refinement architecture | [`core/refinement/contracts.py`](../core/refinement/contracts.py), [`core/refinement/prompts.py`](../core/refinement/prompts.py), [`core/refinement/evidence.py`](../core/refinement/evidence.py) | `tests/test_refinement_conversation.py`; official sources and retrieval limitations are recorded in the prompt document |
| VLM and image-edit routing | [model-catalog.md](./model-catalog.md) | [`config/ai_gateway.yaml`](../config/ai_gateway.yaml), [`core/ai/configuration.py`](../core/ai/configuration.py), [`core/ai/gateway.py`](../core/ai/gateway.py), `GET /api/v1/ai/models` | `tests/test_api.py`, `tests/test_ai_gateway_config.py`, `tests/test_ai_readiness.py`, `tests/test_litellm_gateway.py`, `tests/test_documentation_consistency.py` |
| Production local face swap | [facefusion-local-swap.md](./facefusion-local-swap.md) | [`pipeline.py`](../core/pipeline.py), [`face_execution.py`](../core/face_execution.py), [`model_router.py`](../core/model_router.py), [`occluder.py`](../core/occluder.py), [`region_parser.py`](../core/region_parser.py) | `tests/test_facefusion_pipeline.py`, `tests/test_swapper_policy.py`, `tests/test_task_swap_outcome.py` |
| Multi-person assignment | [facefusion-local-swap.md](./facefusion-local-swap.md) | `resolve_person_mapping_assignment()` in [`core/face_assignment.py`](../core/face_assignment.py), called by [`core/pipeline.py`](../core/pipeline.py) | `tests/test_multi_person_assignment.py`, `tests/test_facefusion_pipeline.py` |
| Portrait library and default reference selection | [`portrait-library.md`](portrait-library.md) plus this policy | [`api/portraits/library.py`](../api/portraits/library.py), [`web/src/utils/portraitAssetSelection.ts`](../web/src/utils/portraitAssetSelection.ts) | `tests/test_portrait_library.py`, `web/src/utils/portraitAssetSelection.test.ts`; scoped M01 evidence in [versioned record](validation/20261014-portrait-reference-evidence.md) |
| Multi-row person labels | README summary plus this policy | [`web/src/utils/faceTopology.ts`](../web/src/utils/faceTopology.ts) | `web/src/utils/faceTopology.test.ts` |
| Comparison geometry and face overlays | README summary plus this policy | [`web/src/utils/imageViewport.ts`](../web/src/utils/imageViewport.ts), `ComparisonViewer.tsx`, `TargetCanvas.tsx` | `web/src/utils/imageViewport.test.ts`, `ComparisonViewer.test.tsx`, `TargetCanvas.test.tsx` |
| Batch task history and result review | [task-history.md](./task-history.md) | [`api/tasks/batch.py`](../api/tasks/batch.py), [`api/tasks/persistence.py`](../api/tasks/persistence.py), [`web/src/features/history/utils/taskHistory.ts`](../web/src/features/history/utils/taskHistory.ts) | `tests/test_api.py`, `tests/test_task_restart_persistence.py`, `web/src/features/history/utils/taskHistory.test.ts`, `RecentJobsPopover.test.tsx` |
| Studio projects and explicit recovery | [studio-projects.md](./studio-projects.md) | [`store.py`](../api/projects/store.py), [`recovery.py`](../api/projects/recovery.py), shared [`task provenance`](../api/tasks/provenance.py), [`projectController.ts`](../web/src/features/projects/projectController.ts) | `tests/test_projects.py`, `tests/test_task_provenance.py`, `web/src/features/projects/projectController.test.ts`, `projectSnapshot.test.ts` |
| Historical refinement quality archive | [delivery-quality.md](./delivery-quality.md) | [`api/refinement/legacy.py`](../api/refinement/legacy.py), [`api/refinement/legacy_quality.py`](../api/refinement/legacy_quality.py) | `tests/test_legacy_refinement.py`, `tests/test_legacy_refinement_api.py` |
| Offline benchmarks and historical measurements | [swap-benchmarks.md](./swap-benchmarks.md) | `scripts/compare_source_strategies.py`, `scripts/evaluate_swap_quality.py`, `scripts/diagnose_batch_strength.py` | `tests/test_source_strategy_comparison.py`, `tests/test_swap_benchmarks.py`, `tests/test_diagnose_batch_strength.py` |
| Engineering boundaries and cleanup policy | [engineering-architecture.md](./engineering-architecture.md) | package boundaries, `config/ai_gateway.yaml`, `scripts/download_models.py` | `tests/test_package_boundaries.py`, `tests/test_ai_gateway_config.py`, `tests/test_download_models.py`, `tests/test_documentation_consistency.py` |

## 事实文档的写作规则

1. **只写当前事实。** 不写“以前/此前/原先/现在/不再/已退役”等历史变迁词；变化的来龙去脉写进 `CHANGELOG.md` 或决策笔记。确需说明兼容边界时，写成现行规则（“旧记录按 X 处理”）而不是叙述变迁。
2. **每个事实只有一个主文档。** 其他地方给一行摘要加链接，不复制整段；不手抄 API 列表、字段清单、模型清单，链接到代码、配置或测试。
3. **按职责分文件，不按时间堆叠。** 不用“2026-09-30 补丁”这类按日期追加的章节承载规则；新增行为写进对应主题的章节。
4. **限制长度。** 单篇文档有字符上限（见下文检查），超出就拆分或压缩；存量超限文档的上限只降不升。
5. **能力声明要有证据。** 见下节。

## 证据要求

文档中的主张至少由下列之一支撑：

- schema、配置项或生产代码路径；
- 断言该行为的回归测试；
- 记录了输入、指标和校验和且可复现的真实图片/模型资产验证；
- 第三方模型限制、许可或算法的上游一手来源，且影响生产行为时要有本地兼容测试。

不得据文件名、未使用的模块、Prompt 文案、旧变更记录或供应商营销名推断“已支持”。“已配置”指在生效的网关配置中存在；“生产路径”指从当前 API/CLI 可达；“已验证”指证据和复现路径已记录。

LiteLLM 以官方 [Router 文档](https://docs.litellm.ai/docs/routing) 和 [图像编辑文档](https://docs.litellm.ai/docs/image_edits) 为准：原生 provider 的支持范围按 endpoint 核实，支持 chat 或任意 base URL 不能证明支持图像编辑；模型示例可替换，不是已验证的服务清单；默认 `size: auto` 不是 4K 承诺，未知价格不得写成免费；离线 Router 测试和配置就绪检查不证明付费上游可用。文档检查不得读取真实 `.env`、发起付费调用或修改运行时数据。

## 决策笔记

只在非平凡改动时写：改变默认值、模型或供应商选择、状态机、存储或失败语义、安全边界，排查出会再次踩的坑，或明确拒绝了一个看起来合理的方案。格式、命名、“只增不改”规则见 [.agents/notes/README.md](../.agents/notes/README.md)。不维护全局索引，避免多分支并行时的合并冲突。测试保证当前行为，笔记保证将来的 Agent 记得原因，二者不重复。

## 变更清单

1. 从上表找到权威文档；
2. 同一变更里更新代码、权威文档、README/DESIGN 摘要和 `CHANGELOG.md`；
3. 非显而易见的主张链接到代码、配置、测试或可复现记录；
4. 删除或替换架构时移除冲突的现行描述，不得让两套流程在不同章节同时生效；
5. 属于非平凡改动的，新增一篇决策笔记；
6. 能机械验证的不变量写进测试；
7. 运行 `uv run pytest tests/test_documentation_consistency.py tests/test_documentation_governance.py`；改 Python 时运行 Ruff 与相关回归，改前端时运行检查、测试和构建。

## 机械检查

- `tests/test_documentation_consistency.py` 检查高风险的重复事实、配置与当前契约；本地文件、章节和指向本仓库 main/Wiki 的链接由非生产工具 [`documentation_links.py`](../scripts/documentation_links.py) 统一离线解析，覆盖根入口、公共协作页、`docs/` 子目录与 Wiki 源码，不访问运行数据或外网。
- `tests/test_documentation_governance.py` 检查字符上限（存量上限只降不升）、事实文档的历史变迁词、笔记结构和禁止全局索引。`docs/**/*.md` 与决策笔记必须能沿真实链接从 README、DESIGN、AGENTS、公共协作入口或 Wiki 首页/平台导航抵达；文件名提及、代码示例、孤立互链与仅有 Changelog 入链不算可发现。
- [`test_documentation_links.py`](../tests/test_documentation_links.py) 覆盖解析与导航反例，公开页/Wiki 专项回归保留各自的发布契约。检查归属和导航取舍见 [决策笔记](../.agents/notes/platform/20261014-rooted-documentation-navigation.md)。
- 这些检查补充评审，不证明正文语义正确。
