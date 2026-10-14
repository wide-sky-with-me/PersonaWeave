# 历史精修质量档案与评价口径

本文说明单任务精修历史档案的读取、人工评价和接受接口，不定义新工作台的生成或表单。生产流程见 [对话式 AI 精修契约](ai-refinement.md)，本地处理见 [本地换脸契约](facefusion-local-swap.md)。

交付质量须由人核验。像素变化、生成成功或 VLM（视觉语言模型）意见都不等于成品率。

## 现行流程与历史兼容的边界

- 生产使用 session / turn / version（会话/轮次/版本）：分析读原图、当前图和逐人参考板；生成只收显式基图这一张整图与文字；复评只提意见，由人最终接受。
- 旧逐人 `allowed_changes`、计划预览、单任务生成与重试不是备用路径；旧 review / plan / create / cancel / retry 返回 `410`。
- 会话接受更新 `accepted_version_id`，不补造满意度评价，不写入旧 task 质量统计。新工作台不使用旧单任务质量表单。
- 历史人工评价和接受发布独立：保存评价不发布候选，接受旧候选不代填满意度。缺真实评价时不宣称成片率提高。

## 旧档案的边界与保留

`api/refinement/legacy.py`、`api/routes/legacy_refinement.py` 只提供旧任务/候选/证据读取、人工评价及按字节人工接受。旧状态和字段仅描述档案，不属于会话轮次生命周期。

**证据与发布：**`outputs/refinement_evidence/{task_id}/` 是只读证据，不是已发布输出。发布使用已验证的暂存字节，不能用档案内容替代；收集证据和保存评价都不接受候选。旧候选接受成功后清理旧活动 staging（暂存区），保留独立的内容寻址质量证据。

**命令兼容：**旧 `POST /refinement/review`、`/refinement/plan`、`/refinement/tasks`、`/refinement/tasks/{task_id}/cancel`、`/refinement/tasks/{task_id}/people/{person_id}/retry` 返回 `410`。不翻译请求，不经影子流水线生成，不静默映射失效别名；旧核心服务/计划和任务生成器不是生产回退。继续编辑须从已验证可用的证据显式新建会话。

**查找记录：**`GET /api/v1/refinement/legacy-tasks?limit=&offset=` 返回有界轻量摘要，不暴露完整请求或图片载荷，无须猜任务 ID。档案与会话轮次分离；旧评价不是新会话接受授权。

**显式删除：**`DELETE /api/v1/refinement/tasks/{task_id}?expected_revision=` 要求当前 revision（状态修订号），且状态为 `REVIEW_REQUIRED`、`ACCEPTED`、`FAILED` 或 `CANCELLED`。它删除旧请求/快照、候选、已接受文件，以及该任务的 initial/generation/candidate/evaluation 质量记录和证据，不触碰会话存储。

历史页支持单条与选择删除，成功后关闭已删质量详情；文件操作沿用私有隔离与回滚边界。保留 ID `sessions` 禁止删除，因为该目录属于会话流程。回归见 `tests/test_legacy_refinement.py`、`tests/test_legacy_refinement_api.py`、`LegacyQualityHistory.test.tsx`。

档案仅在持久旧记录仍有需要时保留；移除需明确保留/迁移决定，并同步回归。

## 历史证据读取与人工评价

`api/refinement/legacy_quality.py::QualityEvaluationStore` 负责读取证据、校验访问、提交人工评价和计算历史汇总。生产代码没有 image / initial / start_generation / save_generation / candidate 等旧 worker 写入方法，也不为新会话建旧表记录。测试只用临时 SQLite 和人工构造的内容寻址 fixture（固定测试样本），不写用户档案。

**提交评价：**绑定 `task_id` 和候选 digest（内容摘要）。PUT 校验 `expected_revision`、`candidate_digest`，逐人 ID 须完整对应历史任务。不同候选的评价互不覆盖；不同轮次产生同一 digest 时，后续评价不改写首轮是否 ready 的事实。

人工填写 verdict（交付判断：`ready / needs_work / unusable`，即可交付/需修改/不可用）、failure_stages（问题阶段：`identity / local / refinement`，即身份/本地处理/AI 精修）、逐人身份/自然度满意度、无关修改及说明。未评价布尔项为 `null`，不等于 `false` 或 `true`；AI 不代填。

**缺失证据：**档案可保留原图、最初本地结果、本轮基图与候选摘要关系。最初本地结果不被后续基图取代。缺记录/文件时明确未知，不从当前画布、可变请求 URL 或新会话猜测、补建；详情不带原始请求、data URI（内嵌图片地址）或模型凭据。

独立证据目录不等于活动暂存区或接受发布目录。旧接受后清理 staging 的规则不用于会话；会话保留版本供分支。证据缺失、未被数据库引用、路径越界或 hash 不符时不可用，不作为接受的替代输入。

证据随所属任务显式删除；没有独立的证据清理界面或自动保留期限。管理磁盘时须保持数据库引用与证据一致。

## 保留的历史 HTTP 接口

以下入口由 `api/routes/legacy_refinement.py` 提供，无需 AI 配置：

- GET `/api/v1/refinement/tasks/{task_id}/quality-evaluation`：读取候选证据和人工评价历史。
- PUT 同路径：校验 `expected_revision`、`candidate_digest` 及逐人评价，不生图、不接受。
- GET `/api/v1/refinement/tasks/{task_id}/evidence/{digest}`：校验数据库引用、路径边界、摘要后读文件。
- GET `/api/v1/refinement/quality-summary`：只汇总旧任务，不混入会话，不自动评分。
- GET `/api/v1/refinement/legacy-tasks`：分页轻量摘要，无图片 URL 或请求载荷。

## 历史统计口径

**尝试与费用：**`generation_count` 是历史服务尝试数，不是 task revision、逐人 attempt（尝试次数）或实际收费次数；缺记录为 `null`。`generations` 仅展示保留的尝试输入、状态、耗时及确有的实际模型证据，不随会话生成追加。费用未知为 `cost=null`，不等于免费。

**首轮成片率：**统计单位为 `sample_unit=refinement_task`，同一批次图片开多个任务不去重。`first_pass_acceptance_rate` 为可核验 batch-local（来自批任务本地结果）起点的人工首轮 ready 数，除以人工首轮评价数 `first_pass_denominator`；无评价为 `null`。

全部任务、未评价、来源未知、次数未知和失败尝试单列，不把缺评价显示为 0% 或 100%。身份与自然度分母是已评价的 candidate-person（候选-人物）项，不是独立人物数；未评价布尔项单独计数。

未知起点不因 `generation_count=1` 就认作“本地＋AI 一次成片”；只有 baseline 证据的任务不补造 `initial_local`。

## 现行批次来源解析

`api/tasks/provenance.py::batch_provenance` 是独立的批次来源解析，给会话提供服务端原图绑定，不写历史质量记录。

它识别成功/部分成功本地结果，以及指向同一受控文件的 URL 别名；调用者提供的原图不覆盖服务端关联。缺原请求时，仍保留可核验的批次结果/原图关联，素材和参数未知项为空，不补造身份或来源。

## 验证与结果解读

- `tests/test_quality_evaluation.py`：临时快照、人工评价、陈旧 revision、原图/基图隔离、未知分母及证据校验。
- `tests/test_legacy_refinement.py`、`tests/test_legacy_refinement_api.py`：无 AI 浏览、摘要/路径边界和显式接受。
- `tests/test_task_provenance.py`：受控别名、部分成功、原请求缺失时的真实关联。
- `tests/test_refinement_planning_api.py`：旧生成/计划返回 `410`，不读图、不隐式开启新生成。
- `tests/test_refinement_conversation.py`、`tests/test_refinement_sessions.py`：三类证据、单图生成及会话版本契约。测试通过数不等于真实画质。

浏览器 smoke（冒烟检查）针对已运行工作台，以拦截请求的**离线 session UI fixtures**验证状态、交互和恢复，不需真实批任务、不调付费模型，也不证明真实生成效果。复现命令：

```bash
uv run --no-project --python .venv/bin/python --with playwright python scripts/smoke_refinement_workbench.py --url http://127.0.0.1:3000 --output outputs/diagnostics/ui-check
```

该命令不改项目依赖声明；缺 Playwright 时临时运行环境需获取依赖。现行脚本不接受 `--batch-task`，历史命令不适用于该入口。

## 本地选材实验

[三策略实验](swap-benchmarks.md#source-strategy-comparison)比较同组素材的真实最佳单张、图库聚合和生产 80/20 混合。素材体检只提示重复、低质或疑似离群，不自动删素材、不作身份认证。这是非生产实验，不改变 API 策略枚举、默认强度、遮挡保护或模型目录。

## 2026-09-07 历史实测记录（旧工作流，非当前能力证明）

以下仅说明该日期、版本和样本的记录，不代表当前验收：

- 批次 `pw_task_7e24362539` 图片 1、局部 `face_index=3`（global person=4），用实际 8 张来源、0.9 强度、相同遮罩/颜色参数，在 CPU 完成三臂对照，3/3 有效输出。忽略目录 `outputs/diagnostics/source-strategy-results-20260907/manifest.json` 保存输入 hash 和参数，`face_comparison.png` 是局部三联图。样本仅一个，不据此改默认或指定赢家。
- 3000 工作台的该次记录覆盖旧计划 API、权限失效及身份不足非门禁；旧质量表单用模拟响应验证保存、空评价、三图及不触发接受，未写生产评价。截图在 `outputs/diagnostics/refinement-workbench-smoke/`，`quality-form-mocked.png` 不是 AI 成片；旧接口/表单记录不证明会话 UI 行为。
- 该次未调收费 AI，记录中的 6 个精修任务均未人工评价，首轮评价分母 0、成片率 `null`，旧来源未知。这是历史观测，不是当前数据库统计，也不是 0% 成片率。
