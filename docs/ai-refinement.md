# Conversational Whole-image AI Refinement

本文是 PersonaWeave 对话式整图 AI 精修的权威契约。生产路径是持久化、由人驱动的编辑会话：生成只编辑整图，证据保持不可变，发布必须由人显式接受。

逐人范围表单和单个可变生成任务不是生产入口。本地换脸引擎、遮罩和身份匹配属于独立流程，见 [facefusion-local-swap.md](facefusion-local-swap.md)。

按问题查阅：

- 模型看什么、如何诊断及文本权限边界：[refinement-analysis.md](refinement-analysis.md)。
- 如何生成候选和复评：[refinement-generation.md](refinement-generation.md)。
- 提示词版本与官方依据：[refinement-prompts.md](refinement-prompts.md)。
- HTTP 接口、存储、上限和删除：[refinement-session-api.md](refinement-session-api.md)。
- 工作台操作与编辑器恢复：[refinement-workbench.md](refinement-workbench.md)。
- 模型路由：[model-catalog.md](model-catalog.md)；旧任务的只读质量档案：[delivery-quality.md](delivery-quality.md)。

## Architecture and ownership

各模块按职责分工，不各自维护第二套状态或模型目录：

- `api/routes/refinement_sessions.py`：会话命令、有界摘要、详情、校验后的图片字节，以及可续接的 SSE 通知（服务端事件流）。
- `api/refinement/contracts.py`：HTTP 命令、会话/轮次/版本的对外数据结构和状态值。
- `api/refinement/service.py`：服务端轮次编排与有上限的模型调用。
- `api/refinement/store.py`：SQLite 事实源、不可变图片、并发、事件和按原字节发布。
- `api/refinement/batch.py`：本地结果提交后，按用户授权可接续首个自动轮次。它使用实际加载的参考和稳定的人脸分配，先保存会话关联，再等待已经调度的轮次；等待本身不会再提交轮次。下一张批处理图片等到该轮次结束（包括 `review_required`），不等人工接受，见 [memory-scheduling.md](memory-scheduling.md)。
- `core/refinement/conversation.py`：经 `AIGateway` 执行三类证据的诊断/讨论、单图生成和建议性复评。
- `core/refinement/contracts.py`、`prompts.py`、`evidence.py`：无分数契约、可信提示词、逐人参考板；纯编码、尺寸选择和规范化在 `image_ops.py`。
- `web/src/features/refinement/`：工作台、对话、版本、参考查看和轻量客户端状态。查看版本、选择修改基图、接受最终版是不同操作。

整体是有界的 Python 工作流，没有额外代理服务、任意工具执行器或自主接受循环。网关通过 LiteLLM 原生接口路由，不绑定特定供应商。

## Session, turn and version

三个术语对应不同记录：

- **会话（session）**：冻结原图、本地初稿、稳定人物映射和实际参考图快照。素材库的后续编辑不改变这些证据。
- **轮次（turn）**：记录一次用户请求、显式 `base_version_id`（本轮修改基图）、模式、诊断、计划、生成元数据和复评。
- **版本（version）**：不可变图片记录，具有独立 ID、父版本 ID、digest（内容摘要）和图片地址；本地初稿也属于版本。

查看历史版本既不改变下一轮基图，也不发布图片。显式选择旧版作为基图会创建分支，不覆盖旧版，不静默换成最新候选。

服务端单独保存 `accepted_version_id`（已接受版本指针）。后续候选不会自动替换已接受结果。

## Execution modes and status

**模式由后端选择，模型只提供分析或图片：**

- `discuss`：分析、回答或提议，绝不生图。
- `generate`：分析显式基图和当前要求；仅在计划可执行且人物对应明确时，至多生成一个候选。
- `auto`：用户授权的首轮自动流程；仅有证据支持的问题和可执行计划才可生图，至多一个候选。无问题、无需修改或人物对应不确定时，说明原因后结束。

已完成的本地结果可经批处理接续触发授权的自动首轮。本地成功不等于 AI 成功或人工接受；只选择本地处理也不授权 AI 接续。

**工作台确认与直接命令的区别：**

工作台生成命令带 `diagnosis_turn_id`，指向人已确认的分析。服务端在模型任务入队前核对会话归属、同一基图、已完成的讨论和明确的人物对应（session ownership, exact base, completed discussion and unambiguous mapping）。绑定同时写入轮次和幂等指纹，即用于判断命令是否重复的摘要；即便存在更新的分析，也只把这一诊断作为 `previous_diagnosis`。

API/CLI 直接 `generate` 且未带确认分析 ID 时，仍是一次显式的“分析并生成”命令（API/CLI direct `generate` without a confirmed-analysis ID）。批处理 `auto` 的授权单独处理；两者都不伪造工作台确认。回归见 [交互测试](../web/src/features/refinement/components/SessionWorkbench.test.tsx)、[会话测试](../tests/test_refinement_sessions.py)。

**执行状态与人工接受分开：**

会话状态为 `idle`（空闲）或 `running`（执行中）。生成轮次的正常路径是：

`queued → analyzing → generating → reviewing → review_required`

讨论、无需修改或等待澄清的轮次在分析后进入 `completed`。失败为 `failed`；取消走 `cancel_requested → cancelled`。

`review_required` 表示执行结束、等待人工判断，不是再次生成指令。新轮次使用新 ID 和显式基图。接受只更新会话的接受指针及发布记录，不是模型输出的轮次状态。

**并发、取消与重启：**

每个会话最多一个活动轮次，服务端也限制并发会话数。离开页面不停止后台工作；取消需显式命令和 revision（状态修订号）校验，已经发往上游的请求仍可能收费。

重启不自动补发付费操作。中断轮次若没有持久化候选，则失败或取消；若已有候选，则保留图片并带中断提示交给人工复评。

## Waiting limits

这些数值是等待上限，不是耗时估计或重试次数：

- 默认网关请求：300 秒。
- 分析和复评：各有 330 秒的外层截止时间（330-second outer deadline），为证据准备和响应校验留出余量。
- 图像编辑：部署超时 600 秒，生成阶段截止时间 900 秒。

可执行来源见 [网关配置](../config/ai_gateway.yaml)、[会话执行器](../api/refinement/service.py)。

## Evidence

模块职责与代码位置见本页 Architecture and ownership。主要回归：`tests/test_refinement_conversation.py`、`tests/test_refinement_sessions.py`、`tests/test_batch_refinement.py`、`tests/test_legacy_refinement.py`、`tests/test_refinement_image_ops.py`；网关传输见 `tests/test_litellm_gateway.py`，文档不变量见 `tests/test_documentation_consistency.py`。

[2026-09-11 五人实测](refinement-live-test-20260911.md)记录了两个真实候选、提示词/父版本/摘要校验、保留的复评失败和人工发现。两个候选都因外观漂移及小物体遮挡细节未解决而未被接受。契约测试通过、成功生成候选，都不证明视觉质量达标。

文档规则见 [documentation-governance.md](documentation-governance.md)。
