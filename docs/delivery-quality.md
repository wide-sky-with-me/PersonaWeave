# 历史精修质量档案与评价口径

本文定义**已退役单任务精修的历史质量档案**及仍保留的人工评价接口，不是新工作台的生成或表单设计。现行生产流程以 [对话式 AI 精修契约](ai-refinement.md) 为准，本地处理以 [本地换脸契约](facefusion-local-swap.md) 为准。目标仍是可核验的人工交付质量，不能把像素变化、生成成功或 VLM 意见当成成品率。

## 现行流程与历史兼容的边界

- 新流程使用 session / turn / version：视觉分析读取原图、当前图和人物参考板；生图只接收显式基图这一张完整图片与文字；复评仅提供意见，最终接受由人决定。
- 旧逐人 allowed_changes、计划预览、单任务生成与重试已退役，不是可选备用路径；旧 review / plan / create / cancel / retry 返回 410。
- 新会话的接受更新 accepted_version_id，不能据此自动补造人工满意度评价，也不会被写入本页的旧 task 质量统计。新工作台不沿用旧单任务质量表单。
- 历史人工评价与接受发布仍是独立动作。保存评价不发布候选；接受旧候选不自动填充满意度。缺少真实人工评价时，不宣称成片率提高。

## 历史证据读取与人工评价

`api/refinement/legacy_quality.py::QualityEvaluationStore` 仅负责读取已有证据、校验访问、提交人工评价和计算历史汇总。历史质量生产器已删除：生产代码不再提供 image / initial / start_generation / save_generation / candidate 等旧 worker 写入方法，也不为新会话创建旧表记录。测试使用临时 SQLite 与人工构造的内容寻址 fixture，不向用户档案灌测试数据。

评价绑定历史 task_id 与候选 digest。PUT 使用 expected_revision 与 candidate_digest 检查陈旧请求；覆盖的人物 ID 必须与该历史任务一致。已保留的不同候选评价互不覆盖；旧记录表明不同轮次产生相同 digest 时，后续评价不能改写首轮是否 ready 的事实。

人工字段包括 verdict（ready / needs_work / unusable）、failure_stages（identity / local / refinement）、逐人身份及自然度满意度、是否有无关修改与说明。布尔项未评为 null，不当作 false 或 true；AI 不能代填这些字段。

旧档案可能保留原图、最初本地结果、本轮基图与候选的摘要关联。“最初本地”不能被后来的基图取代。缺少记录或文件时明确未知，不从当前画布、可变请求 URL 或新会话推测、补建证据。历史详情不附带原始请求、data URI 或模型凭据。

只读图像证据位于 `outputs/refinement_evidence/{task_id}/`，独立于活动 staging 与 accepted 发布目录。接受旧候选后清理旧 staging，但不清理独立质量证据；新会话保留历史版本供分支，不能照搬这项旧 staging 清理规则。缺失、未被数据库引用、路径逃逸或 hash 不符的文件不可用；证据归档不能作为接受发布时的替代输入。当前没有档案清理界面或自动保留期限，管理磁盘须保留数据库引用与证据的一致性。

## 保留的历史 HTTP 接口

以下入口由 `api/routes/legacy_refinement.py` 服务，无需 AI 配置：

- GET `/api/v1/refinement/tasks/{task_id}/quality-evaluation`：读取已有候选证据及人工评价历史。
- PUT 同路径：校验 expected_revision、candidate_digest 与逐人评价，不触发生图或接受。
- GET `/api/v1/refinement/tasks/{task_id}/evidence/{digest}`：验证数据库引用、路径边界及摘要后读取文件。
- GET `/api/v1/refinement/quality-summary`：只汇总已有旧任务，不混入新会话，不自动给旧任务评分。
- GET `/api/v1/refinement/legacy-tasks`：分页轻量历史摘要，不携带图片 URL 或请求载荷。

## 历史统计口径

历史 generation_count 是当时服务尝试次数，不是 task revision、某个人的 attempt，也不能直接当实际收费次数；未记录时为 null。`generations` 仅反映已留存的尝试输入、状态、耗时和确有的实际模型等证据，不会随着新会话生成而继续追加。费用无法核实时 cost=null，不能当免费。

汇总单位保持 `sample_unit=refinement_task`，同一批次图片开过多个任务不会去重。`first_pass_acceptance_rate` 为可核验 batch-local 起点的人工首轮 ready 数 / 人工首轮评价数（first_pass_denominator）；没有评价为 null。全部任务、未评价、来源未知、次数未知及失败尝试分别列出，不能把缺失评价伪装成 0% 或 100%。

身份与自然度的分母是已有评价中的 candidate-person 项，不是独立人物数；未评价布尔项单独计数。未知起点不能仅因 generation_count=1 就被认作“本地＋AI 一次成片”；只有 baseline 证据的任务不能补造 initial_local。

## 现行批次来源解析

活跃的 `api/tasks/provenance.py::batch_provenance` 已与历史质量档案分离，供新会话绑定服务端批处理原图。它识别成功及部分成功的本地结果，也识别指向同一受控文件的 URL 别名；不能用调用者伪造的原图覆盖服务端关联。原请求记录缺失时，仍保留可验证的批次结果/原图关联，素材与参数未知项保持为空，不伪造身份或来源。此解析不写历史质量记录。

## 验证与结果解读

- `tests/test_quality_evaluation.py`：临时历史快照、人工评价、陈旧 revision、原图/基图隔离、未知分母与证据校验。
- `tests/test_legacy_refinement.py`、`tests/test_legacy_refinement_api.py`：无 AI 浏览、摘要/路径边界与显式接受。
- `tests/test_task_provenance.py`：受控别名、部分成功及原请求缺失时的真实关联。
- `tests/test_refinement_planning_api.py`：旧生成/计划命令返回 410，不读取图片或隐式开启新生成。
- `tests/test_refinement_conversation.py`、`tests/test_refinement_sessions.py`：现行三证据、单图生成及会话版本契约；不以测试通过数代替真实画质评价。

当前浏览器 smoke 针对已运行的工作台，使用拦截请求的**离线 session UI fixtures**验证状态、交互与恢复，不需要真实 batch task，不调用付费模型，也不证明真实生成效果。复现：

```bash
uv run --no-project --python .venv/bin/python --with playwright python scripts/smoke_refinement_workbench.py --url http://127.0.0.1:3000 --output outputs/diagnostics/ui-check
```

该命令不修改项目依赖声明；若没有 Playwright，uv 的临时运行环境需要获取依赖。`--batch-task` 已移除，不能用历史命令调用现行脚本。

## 本地选材实验

[三策略实验](swap-benchmarks.md#source-strategy-comparison)比较同一组素材的真实最佳单张、图库聚合与生产 80/20 混合。素材体检只提示重复、低质或疑似离群，不能自动删素材或作身份认证。这是非生产实验，不改变 API 策略枚举、默认强度、遮挡保护或模型目录。

## 2026-09-07 历史实测记录（旧工作流，非当前能力证明）

- 原批次 `pw_task_7e24362539` 的图片 1、局部 face_index=3（global person=4），使用实际 8 张来源、0.9 强度、同一遮罩/颜色参数，在 CPU 上完成三臂对照，3/3 有效输出。记录在忽略目录 `outputs/diagnostics/source-strategy-results-20260907/manifest.json`，含输入 hash 与参数；同目录 `face_comparison.png` 为局部三联图。仅一个样本，不据此改默认或指定赢家。
- 当时的 3000 工作台验证过旧计划 API、权限失效及身份不足非门禁；旧质量表单以浏览器模拟响应验证保存、空评价、三图及不触发接受，未写入生产评价。截图保留在 `outputs/diagnostics/refinement-workbench-smoke/`；`quality-form-mocked.png` 不是实际 AI 成片。这些旧接口/表单已退役，不应再用该记录证明当前 UI 行为。
- 当时未调用收费 AI；当时实查的已有 6 个精修任务均未人工评价，首轮评价分母为 0、成片率 null，旧来源未知。这是历史观测，不是当前数据库统计，也不是 0% 成片率。
