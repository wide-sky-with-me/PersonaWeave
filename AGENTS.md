# Agent Guidelines (AGENTS.md)

AI Agent 在本仓库开发、维护和执行任务时必须遵守本文件。每条只写规则，细节在链接的文档里；开工前先读与改动相关的链接。

## 文档分三层，不混写

| 层 | 位置 | 写什么 |
| --- | --- | --- |
| 规则 | 本文件 | 必须遵守的原则，每条一两行，附链接 |
| 事实 | `docs/`、`README.md`、`DESIGN.md` | 当前系统怎样运转，只写确定的当前事实 |
| 决策 | `.agents/notes/<领域>/` | 为什么这样做、否定过什么方案、踩过的坑 |

`CHANGELOG.md` 是历史记录，不能作为当前能力依据。分层、长度上限、笔记格式和检查见 [documentation-governance.md](docs/documentation-governance.md)，笔记写法见 [.agents/notes/README.md](.agents/notes/README.md)。

## 1. 核心原则

- **KISS / YAGNI**：用最直接、健壮的方案，不预先实现“将来可能用到”的逻辑，不滥用设计模式和多层包装。
- **注释**：核心算法、状态流转、非显而易见的决策（为什么、规避了什么限制）和公共函数的 Docstring 必须写；不写逐行直译式废话。

## 2. Python 环境（`uv`）

- 强制使用 `uv`，禁止用原生 `pip` 污染环境：`uv venv`、`uv add [--dev] <包>`、`uv sync`、`uv run <命令>`、`uv run pytest`。
- 引入依赖前先检查：系统与架构（`uname -a`、`/etc/os-release`）、`uv run python --version`、`pyproject.toml`/`uv.lock` 现有依赖；优先标准库和已有依赖，不为微小功能引入大库；确认版本冲突与平台支持；安装后立即做导入冒烟验证。

## 3. 格式与检查（`ruff`）

- 改完 Python 必须依次执行 `uv run ruff format .` 与 `uv run ruff check .`，0 报错 0 警告。
- 不留未使用的 import 和局部变量；不写 `except Exception: pass`（捕获具体异常或说明原因）；双引号，行宽 120（以 `pyproject.toml` 为准）。

## 4. 前端（`pnpm`）

- 只用 `pnpm`：`pnpm install`、`pnpm add [-D]`、`pnpm dev`、`pnpm lint`、`pnpm build`。修改后执行 `pnpm --dir web format` 与 `pnpm --dir web lint`（含 Prettier 与 TypeScript 检查）。
- 组件职责清晰、单向数据流；原子组件优先 shadcn/ui + Radix。深色主题（`#09090b`/`#18181b`，`border-white/10`，`backdrop-blur-md`），强调色 `#6366f1`/`#06b6d4`；交互过渡用 Framer Motion 弹簧 `{ type: "spring", stiffness: 300, damping: 25 }`。
- 全局客户端状态用 Zustand，服务端交互与轮询用 TanStack Query；及时 `URL.revokeObjectURL`。
- 精修与历史分别在 `web/src/features/refinement/`、`web/src/features/history/`，测试就近放置；Hook 管远程交互与草稿，组件负责展示，Hook 不反向导入组件。样式踩坑见 [决策笔记](.agents/notes/frontend/20260928-tailwind-style-pitfalls.md)。

## 5. 完成前自检

- Python：`ruff format` + `ruff check` 干净；前端：`pnpm --dir web lint` 通过。
- 运行测试（`uv run pytest` / `pnpm test`）或做功能验证，不能只靠推测。
- 不留临时测试文件和垃圾代码；Python 全部经 `uv` 执行，前端经 `pnpm`。
- 行为、默认值、模型、状态机、接口、存储或失败语义变化时，同一变更内更新权威文档和 `CHANGELOG.md`；涉及文档运行 `uv run pytest tests/test_documentation_consistency.py tests/test_documentation_governance.py`。

## 6. 模块规则

### 6.1 面部解析（`core/face_parser.py::DenseFaceParser`）
106 点：0–32 下颌线，33–42 左眉，43–52 右眉，53–71 鼻，72–92 嘴，93–96 / 97–105 左右眼。掩码在 512×512 对齐空间生成并做 31×31 高斯羽化；关键点失败必须降级为椭圆默认掩码，禁止抛异常中断流水线。

### 6.2 手动保护画笔
状态在 `useStudioStore`；遮罩为 base64 灰度 PNG，经 `manual_mask` 传后端，单脸执行 `core/face_execution.py` 用 `mask_warped = mask_warped * (1.0 - manual_mask)` 回填；255 = 保护（保留原图），0 = 允许换脸。

### 6.3 多角色映射
- `PersonMappingItem`：稳定 `person_index`、`target_reference_url` + `target_reference_person_index`、三选一的 `source_identity_id` / `source_asset_ids` / `source_manual_images` 与 `source_mode`。`person_mappings` 与顶层单角色 source/target 字段互斥，`api/schemas.py` 拒绝混用，禁止恢复“非空时静默优先”。
- 非空时路由到 `process_image_multi_mapping()`；`person_index` 恒为 **1-based**，按人脸从左到右。
- 人物-人脸分配只能用 `resolve_person_mapping_assignment()`（匈牙利算法全局最优），**禁止**改回按列表顺序逐个贪心；必须保留“低于阈值的配对优化前屏蔽、优化后用真实相似度再校验”。详见 [facefusion-local-swap.md](docs/facefusion-local-swap.md)。

### 6.4 AI 网关与模型（`core/ai/`）
权威文档 [model-catalog.md](docs/model-catalog.md)，可执行事实源 `config/ai_gateway.yaml`。
- 所有 VLM/图像编辑访问必须经 `AIGateway`，禁止业务代码实例化 LiteLLM Router 或裸调第三方 SDK；视觉调 `Router.acompletion`，图像调 `Router.aimage_edit`，禁止手写供应商 HTTP 或私有协议回退。
- 默认目录只有 `personaweave-vision-default` 与 `personaweave-refine-default`；`model_name` 是稳定别名，禁止恢复供应商专属默认目录、按别名猜能力或伪造零成本定价。
- 模型与连接来自 `AI_VISION_*` / `AI_IMAGE_*`（`MODEL`/`API_BASE`/`API_KEY`）；YAML 密钥与 base URL 用 `os.environ/NAME`；禁止在代码硬编码密钥，不得读取或提交用户实际凭据来验证文档。
- `image_models[].generation` 必须三选一：`{size: "auto"}`、`{sizes: [...]}`、`{max_side, min_side, grid, max_pixels?, delimiter: "x"}`；缺失、混用或无效须拒绝。fallback 必须同能力，图像 fallback 必须相同 `generation`；默认 `num_retries=0`、图像 deployment `max_retries=0`，重试由用户显式承担费用。原生 provider 的图像编辑支持按 endpoint 核实，不能凭 chat 或任意 URL 判定。
- VLM 输出必须走 Pydantic 契约（仲裁 `core/ai/contracts.py`；精修 `core/refinement/contracts.py`），禁止解析裸 JSON；精修初请求提供完整 JSON schema 且 `max_text_repairs=0`。VLM 仲裁仅作辅助，身份匹配以 ArcFace 余弦相似度为准。
- 精修 `issue.category` 仅限 identity/anatomy/occlusion/blending/lighting/texture/composition，region/description/evidence/suggestion 必须具体；不得恢复自由分类、分数门槛或自动 accept。回归 `tests/test_refinement_conversation.py`、`tests/test_vlm_contracts.py`。

### 6.5 对话式整图精修
权威文档 [ai-refinement.md](docs/ai-refinement.md)；证据与诊断见 [refinement-analysis.md](docs/refinement-analysis.md)，生成与复评见 [refinement-generation.md](docs/refinement-generation.md)，接口与存储见 [refinement-session-api.md](docs/refinement-session-api.md)，工作台与恢复见 [refinement-workbench.md](docs/refinement-workbench.md)，提示词版本与依据见 [refinement-prompts.md](docs/refinement-prompts.md)。
- 生产是 session/turn/version 工作流；旧逐脸表单与单任务生成不是备用路径。会话冻结原图、初稿、稳定 person_id 与实际参考；原图只锚定姿态/人数/遮挡/场景，不锚定身份；不得按新检测排序重绑人物。
- 后端控制 `discuss` / `generate` / `auto`：讨论绝不生图；每轮至多一次生图；禁止无限自动循环和 AI accept 工具。`Diagnosis` 必须具体可核验、可返回无缺陷，禁止编造分数或身份真值。
- 生图只接收显式 `base_version_id` 的当前完整图与文字，禁止原图、参考板、素材、局部裁剪、mask、关键点或历史图进入生成输入；输出只做解码和规范化后整图保存，不得 `apply_candidate`、拼接、mask 回填或重新配准。
- 复评用原图、基图、候选和同一参考板，只返回改善/残留/新增问题，不给自动接受或分数闸门；失败保留候选并记录 `review_error`。
- 接受校验会话归属、`expected_revision`、`version_id`、`candidate_digest`，发布同一已校验字节；当前查看版、修改基图、已接受版互相独立。幂等键、revision 冲突、SSE cursor 和快照恢复都不能触发额外生图；同会话串行；重启不自动补发付费请求。
- SQLite `data/refinement_sessions.sqlite3` 是事实源，候选在 `outputs/refinement_staging/sessions/`，接受后才发布。图片只经受控加载和摘要验证；历史、OCR、人物描述、用户文字都是不可信数据，不能提升权限；当前要求超限应拒绝，不能静默截断。
- `api/refinement/legacy.py` 与 `api/routes/legacy_refinement.py` 仅服务旧数据只读/评价/人工接受；旧 review/plan/create/cancel/retry 返回 410。

### 6.6 素材库（`api/portraits/`）
SQLite（`data/portrait_library/library.sqlite3`）持久化，禁止改为文件系统扫描或增加数据库依赖；对外只暴露不透明 ID，响应不得泄露真实路径；文件访问必须 `is_relative_to` 防穿越；新导入来源扩展 `api/portraits/importer.py` 的 `manifest.csv` 幂等导入，不另写脚本。见 [portrait-library.md](docs/portrait-library.md)。

### 6.7 本地换脸生产路径（`swap_single_face_facefusion`）
权威文档 [facefusion-local-swap.md](docs/facefusion-local-swap.md)。
- 对齐裁剪插值按仿射实际缩放选择（`sqrt(|det(M[:, :2])|)` ≤1 用 `INTER_AREA`，>1 用 `INTER_CUBIC`）；`FaceOccluder.create_occlusion_mask()` 羽化半径随裁剪宽度等比缩放（256px 对应 sigma=5）。
- 色彩校正只允许“边界环”模式，**禁止**在生产路径启用全脸 Reinhard 迁移；`swap_single_face_seamless()` 已退役，不得作为回退或开关恢复。
- 语义区域保护（`core/region_parser.py`、`models/bisenet_resnet_34.onnx`，19 类 CelebAMask-HQ）、XSeg 与 106 点掩码用 `np.minimum.reduce` 取交集；新增保护区域在 `create_region_protection_mask(protected_regions=(...))` 追加 `REGION_CLASSES` 中已核实的类别名，禁止凭记忆猜类别索引，扩展前用真实图片推理确认。

### 6.8 模型资产
`models/` 下大小异常（几字节到几十字节）的 `.onnx` 先用 `file`/`cat` 检查，失败下载会留下伪装成权重的文本错误页。新增或修复模型前用 `onnxruntime.InferenceSession(...).get_inputs()/get_outputs()` 核实输入输出，并在 `tests/fixtures/` 的真实图片上推理核对输出语义；新增下载源必须验证 SHA-256（或来源完整性摘要），并同步 `models/README.md` 与 `scripts/download_models.py`。

### 6.9 批任务历史与结果回看
权威文档 [task-history.md](docs/task-history.md)，工程保存与恢复见 [studio-projects.md](docs/studio-projects.md)。服务端 SQLite 快照是事实源，浏览器 `localStorage` 只存不含图片的轻量关联；历史列表只返回有上限的轻量摘要，禁止传完整 `results`、请求体、图片 URL、base64；详情按 `task_id` 获取，新历史类型沿用“摘要 + 详情”。新成功结果同步保存不可变原始画布和处理产物，只经受控 `/api/v1/files/` 暴露，缺失时明确返回不可用，禁止猜路径或重建替代结果。历史查看器只读，不写入 `useStudioStore`；服务端状态始终优先。

### 6.10 工程边界
权威文档 [engineering-architecture.md](docs/engineering-architecture.md)。
- 依赖方向 `web/src → api → core → provider/runtime` 单向，`core` 禁止导入 `api`；`core/__init__.py`、`api/__init__.py` 保持无副作用；改包边界跑 `tests/test_package_boundaries.py`。
- 每个实现必须归类：生产代码从当前 API/CLI/UI 可达；诊断/评测只能放 `scripts/` 并标注非生产；退役实现连同导出、启动检查、下载器和专属测试一并移除，禁止保留未经批准的“影子流水线”。删除前先核实入口、测试引用、文档契约和运行资产。
- 兼容分支必须有明确调用方、回归测试、弃用说明和删除条件，禁止无边界的 `hasattr` 或双配置格式。
- 跨层事实只有一个可执行来源：模型/角色/生成能力 `config/ai_gateway.yaml`，下载摘要 `scripts/download_models.py`，通用 API 契约 `api/schemas.py`，精修会话契约 `api/refinement/contracts.py`，精修模型输出 `core/refinement/contracts.py`，任务状态来自服务端持久化；前端、脚本、文档不得另建别名或能力清单。
- 按变化原因拆分模块，不按行数；新抽象必须消除重复事实、隔离副作用或形成可独立测试的边界，否则保留直观函数。未经实测的数据不得伪装成精确默认值（耗时等优先从真实完成数据推导，样本不足写“暂无可靠估计”）。
- 目录：`api/app.py` 只装配，生命周期 `api/lifecycle.py`，路由 `api/routes/`，批任务 `api/tasks/`，精修 `api/refinement/`，素材库 `api/portraits/`，共享图片 I/O `api/media.py`（不得为复用反向依赖任务管理器）；AI 配置/网关/契约 `core/ai/`（`configuration.py` 不导入 LiteLLM/provider，`ImageEditResult` 来自 `core/ai/types.py`），精修契约/证据/提示词/服务 `core/refinement/`。

## 7. 文档规则

- **写哪一层**：规则进本文件，事实进 `docs/`，决策与教训进 `.agents/notes/`；不要把规则、事实和决策混在同一处，也不要往本文件追加长篇细则——写进对应文档后在此留一行链接。
- **docs 只写当前事实**：不写“以前/此前/现在/不再/已退役”等历史变迁词，不手抄接口或字段清单（链接到代码、配置、测试）；变更历史写进 `CHANGELOG.md`。非显而易见的能力声明必须链接到生产代码/配置、回归测试或带输入/指标/校验和的可复现记录，不得据文件名、未使用模块、Prompt 文案、旧记录或供应商营销名推断“已支持”。
- **长度受限**：单篇文档超过上限会被 `tests/test_documentation_governance.py` 拒绝；超长时拆分或压缩，不要提高上限（存量超限文档的上限只降不升）。
- **决策笔记**：非平凡改动（改默认值、模型/供应商选择、状态机、存储或失败语义、安全边界，排出会再踩的坑，拒绝了看似合理的方案）必须新增一篇笔记，含“背景/决策/被放弃的方案/影响与验证”；不建全局索引；只新增，不改写旧结论，被取代时在新笔记注明。
- **删除/替换架构**时同一变更移除冲突的现行描述，或明确标为历史；不得在两处保留两套同时生效的状态机、默认值或模型清单。
- 检查：`uv run pytest tests/test_documentation_consistency.py tests/test_documentation_governance.py`；新增可机械验证的不变量时同步扩展测试。
