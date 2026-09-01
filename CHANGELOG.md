# Changelog

本文件记录 PersonaWeave 的版本演进与功能变更历史。

---

## [Unreleased] - 2026-08-31

### 🧹 里程碑工程化整顿

- 新增 `docs/engineering-architecture.md`，确立 `web → api → core → provider/runtime` 单向依赖、生产/诊断/退役代码分类、兼容分支退出条件、按变化原因拆分模块和 AI 开发防回归规则；对应硬约束已同步写入 `AGENTS.md`。
- 删除与现行整图精修契约冲突且无生产调用方的 `core/localized_refinement.py`，以及仅供旧诊断脚本使用的 `core/enhancer.py` 和三个 CodeFormer/GFPGAN 脚本；启动预检、默认下载器、README/DESIGN/模型说明同步移除退役资产依赖。
- `core/__init__.py` 与 `api/__init__.py` 改为无副作用包入口；导入 schema/AI 工具不再隐式加载 FastAPI、InsightFace 和完整 ONNX 流水线，并新增包边界回归测试。
- AI Gateway 移除无调用方的旧 `image_edit` 单模型配置。VLM 的默认/身份仲裁/精修角色与每个图像模型的生成尺寸能力统一由 `config/ai_gateway.yaml` 声明并在加载时校验；前端和模型评测脚本不再维护硬编码别名列表。
- `TargetCanvas` 的保护画笔副作用与 mask 序列化提取到 `useManualProtectionBrush`；`RefinementModal` 的人脸缩略图、单人报告与整图复评提取到纯展示组件，避免 UI 展示改动触碰任务编排。
- Yaw、参考图质量、输出可见变化与极端侧脸兼容性从流水线编排器迁入纯领域模块 `core/face_metrics.py`，API、流水线、脚本和测试共享同一规则，移除 API 中的函数内绕行导入。
- 默认模型下载新增 SHA-256 强校验；同名大文件不再只凭大小判定为有效权重。批任务预计耗时改为基于近期真实 `cost_ms` 中位数，没有样本时返回 0 表示暂无可靠估计。

### 📚 文档事实源与同步门槛

- 新增 `docs/documentation-governance.md`，规定“权威文档 → 可执行代码/配置 → 回归测试/真实验证记录”的证据矩阵。`README.md` 是能力摘要、`DESIGN.md` 是架构说明、`CHANGELOG.md` 是历史记录；历史条目不能覆盖当前权威契约。
- 新增 `docs/model-catalog.md`，以 `config/ai_gateway.yaml` 为可执行事实源，记录当前 VLM、图像编辑模型、默认别名和回退链；前端模型列表继续由 `GET /api/v1/ai/models` 动态读取。
- 新增 `tests/test_documentation_consistency.py`，机械校验精修状态机、模型目录与 YAML、素材默认数量及本地相对链接。行为变更必须同步更新代码、权威文档、摘要、变更记录和证据测试。

### ✅ 当前实现校准

- AI 精修新任务状态统一为 `QUEUED → REFINING → REVIEWING → REVIEW_REQUIRED → ACCEPTING → ACCEPTED`；人物定位和 VLM 诊断发生在任务创建前，`LOCALIZING` 仅用于读取旧任务，不存在新任务的 `PREFLIGHTING` / `DIAGNOSIS_REQUIRED` 状态。
- 精修 VLM 使用专用 `personaweave-vision-refinement` 别名及其配置回退链；图像编辑默认 `personaweave-refine-seedream`，完整候选项以网关 YAML 和模型目录为准。
- 前后对比与人脸框共用可复验的 viewport 几何；多排合影在界面上按行显示位置文案，但 API 的稳定 `person_index` 仍保持全局从左到右编号。
- 人像库默认最多选 8 张，按正脸、左右侧脸和俯仰姿态分层；极端侧脸缺少同方向素材时跳过该人物，并在同步/批量结果和前端展示部分成功警告。M01 扩展素材的 SHA-256、SCRFD 姿态与 ArcFace 相似度记录在 `data/portrait_expansions/README.md`。
- VLM 瑕疵输出继续由固定七分类和具体性约束校验；生成成功但复评失败时保留 staging candidate，并用提示性 `review_error` 交给人工判断。

### 🆕 持久化任务历史与结果回看

- 新增 `GET /api/v1/task-history/batch-tasks` 的 `BatchTaskSummary` 轻量投影，限制 `limit=1..100`；新列表不传输完整 results、图片 URL 或可能很大的 data URI，用户打开具体任务时才请求详情。旧 `GET /api/v1/batch-tasks` 完整列表标记为 deprecated 并保留响应形状，避免破坏既有调用方。
- 新成功结果把解码后的原始画布和换脸图片一同写入任务目录，并补齐终态 `completed_at`，避免原始上传 URL 失效后无法进行前后对比。
- Web Studio 任务历史合并服务端状态与本机轻量标签；完成/部分完成任务支持多结果切换、卷帘对比、50%–300% 缩放、打开和下载。历史查看严格只读，不污染当前工作台；已完成任务不再自动恢复成活动工作区。
- 架构与复验证据见 `docs/task-history.md`。该功能沉淀了“服务端事实源、摘要/详情分离、提交时保存复核产物、历史与编辑器隔离”的通用设计约束。

### 📐 AI 精修整图直出架构（文档批准）

- 新的权威设计见 `docs/ai-refinement.md`：人物选择/定位预检后，以一次整图 VLM 调用整理每人瑕疵与文字可见定位；用户补充指令后，图像模型只接收当前有瑕疵的整图，不接收身份参考图、裁剪、mask 或关键点图。
- provider 整图结果规范化后直接保留，不再以 `apply_candidate()`、关键点对齐、mask 或局部合成为必经路径；后续一次整图 VLM 复评仅供用户决策参考。
- staging、revision/digest、取消/重试/恢复、安全图片加载与原子发布约束继续保留。以下旧版本条目是历史记录，不代表现行精修架构。

### 🐛 精修生图 Prompt 丢失完整瑕疵清单

- 根因：VLM 诊断（`/refinement/review`）返回的每人 `issues`（结构化瑕疵清单，最多6条，每条含 category/region/description/suggestion）只用于前端展示；创建精修任务时前端只把单句摘要 `recommended_prompt` 存进 `review_prompt` 一起提交，`RefinementPersonRequest` schema 本身也没有 `issues` 字段。真正送进生图模型的 `build_whole_image_prompt()` 只读 `review_prompt`/`user_instructions` 两个单句字段，完整瑕疵清单从未到达生图模型——一个人若有多条不同部位的瑕疵，最终大概率只有 1 条（或被压缩后的笼统描述）真正进了 Prompt。
- 修复：`RefinementPersonRequest` 新增 `issues: list[RefinementIssue]` 字段；前端创建任务时把 `diagnoses[id].issues` 一并提交；`build_whole_image_prompt()` 逐条列出每条瑕疵（`{序号}. [category] region：description（修复建议：suggestion）`），标注"须逐项修复，不得只挑其中一条"，`review_prompt` 降级为补充性的"总体建议"。未提供 `issues` 时行为不变（向后兼容）。

### 🗃️ 已退役迁移记录：AI 精修发际线局部合成

- 根因：106点解剖学 mask（`generate_anatomical_mask_512`）的 `forehead_lift = face_h * 0.35` 是几何估算，对刘海/碎发盖住额头的发型会越界画进头发里；AI 精修在这块越界区域贴回的生成像素分辨率、丝缕纹理都和原图头发对不上，在发缝/额角处形成灰蒙蒙的糊斑。
- 修复：`LocalizedRefinementEngine` 新增可选 `region_parser` 参数，接入已有的 BiSeNet 语义解析（`core/region_parser.py`），用 `hair`/`hat` 语义分类结果把解剖学 mask 中越界到头发的部分裁掉，确保精修合成绝不覆盖真实头发像素。`RefinementService` 已自动把 `pipeline.region_parser` 传给它，无需额外配置。
- 用真实问题图片验证：裁剪出的合成 mask 现在精确贴合皮肤边界，包括刘海丝缕间的细小缺口都能正确避开。

### 🗃️ 已退役迁移记录：旧局部精修分辨率策略

- 根因：`refine_whole_image()` 之前固定把生成请求的最长边压到 2048px，再用 LANCZOS4 硬放大回原图分辨率（此次问题图片原始 5424×3616，放大约 2.65 倍），合成进画面的区域必然比周围原生像素软。
- 已核实：Seedream（默认别名）文档支持到约 4K；但 Qwen-Image-Edit-Plus/Max（可在精修重试时手动选用）请求分辨率硬上限是 2048px，超过会被直接拒绝。因此不能用同一个数字统一放宽。
- 修复：新增 `RefinementService._generation_max_side()`，按实际解析出的 provider 区分上限——Seedream 用 3072px，Qwen-Image-Edit-Plus/Max 及其余 provider 保持 2048px 不变，避免选用 Plus/Max 重试时因请求尺寸超限被服务端拒绝。

### 🆕 瑕疵点 VLM 诊断规范化（更具体、可验证）

- `core/vlm_contracts.py` 的 `ReviewIssue.category` 从自由字符串改为固定七分类枚举（`anatomy_geometry`/`bilateral_symmetry_gaze`/`skin_texture`/`boundary_blending`/`lighting_color`/`pose_perspective_scale`/`synthesis_artifact`），`region`/`description` 增加最短长度约束，拒绝"脸部"/"有点怪"这类空泛回答。
- `RefinementService.review()` 的系统 Prompt 新增"不合格 vs 合格"瑕疵描述 few-shot 范例，强制 `region`/`description`/`suggestion` 用中文输出，并要求 `description` 同时给出外观特征、相对范围估计与对比基准三要素。`recommended_prompt` 要求复用 `issues` 中出现过的精确部位命名，避免整体描述又退回笼统措辞。
- `RefinementService.review_local_candidate()` 的 `issues`（字符串数组）同步收紧为中文 + 具体解剖学定位，不再接受孤立判断词。
- 前端无需改动：`RefinementModal.tsx` 已经按 `{region}：{description}；{suggestion}` 拼接展示，具体性提升会直接体现在现有 UI 上。

### 🆕 BiSeNet 语义区域保护（眼镜）

- **新增 `core/region_parser.py`**（`RegionFaceParser`）：加载 `models/bisenet_resnet_34.onnx`，对齐裁剪人脸做 19 类 CelebAMask-HQ 语义分割，生产路径默认保护 `glasses`（class 6）区域——XSeg 只识别"异物遮挡"（手/道具），并不知道某块像素在语义上是眼镜，换脸时镜框/镜片经常被融化变形而毫无保护。已接入 `swap_single_face_facefusion` 的 mask 交集链（box ∩ 106点解剖学 ∩ XSeg ∩ BiSeNet），模型缺失时优雅降级为 `None`，与 XSeg/106点解析器同一容错模式。
- **模型来源修正**：`models/bisenet_resnet_34.onnx` 与 `models/face_parser.onnx` 此前是两个 15 字节的 `Entry not found` 文本残留（失败下载留下的错误页，并非有效 ONNX 资产），`scripts/download_models.py` 也从未收录它们；`models/README.md` 曾记录过这一事实但文件本身未清理。经核实，`bisenet_resnet_34.onnx` 是 FaceFusion 官方模型（vendor yakhyo，MIT 协议，来自 `facefusion/models-3.0.0`），文件名与占位符完全一致，已重新下载（SHA-256 `4a0b8c958a3c938913bd06a8365dbb3c8761afba6ecbf0d14b3b1f77eb230c96`）并纳入 `scripts/download_models.py` 默认下载列表；`face_parser.onnx` 在 FaceFusion 模型仓库中找不到任何对应资产，已删除。
- I/O 契约（输入张量 `input`，512×512，ImageNet 归一化 NCHW；输出 `(19, 512, 512)` 逐类别 logits）已通过官方源码核对，并在真实测试图片上跑通推理验证。

### 🗃️ 旧架构快照：AI Gateway、局部精修任务与人像素材库

- **AI Gateway** (`core/ai_gateway.py` + `config/ai_gateway.yaml`)
  - 配置驱动的 LiteLLM Router 封装，统一管理 VLM 与图像编辑模型别名（`personaweave-vision-review/precision/fallback`、`personaweave-refine-seedream/plus/max/fallback`），支持 fallback 链与 `os.environ/NAME` 语法解析密钥。
  - 所有别名通过 **DMXAPI**（`https://www.dmxapi.cn/v1`，OpenAI 兼容协议的第三方模型聚合网关）调用；`core/providers/dmx_seedream.py`、`core/providers/dmx_responses.py` 是针对 DMX 私有响应协议的自研 httpx 适配器。
  - `core/vlm_arbiter.py` 迁移为经由 `AIGateway` 调用，不再直接使用 LiteLLM。
  - `core/vlm_contracts.py` 新增结构化输出契约：`FaceReviewReport`、`BatchFaceReview`、`CandidateQualityReview`、`GlobalProtectionReview`、`IdentityArbitration`。

- **局部 / 整图 AI 精修** (`core/localized_refinement.py` + `core/refinement_service.py` + `api/refinement_task_manager.py`)
  - 新增 `/api/v1/refinement/review`、`/api/v1/refinement/tasks`（创建/查询/取消/接受/单人重试）、`/api/v1/refinement/tasks/{task_id}/candidate` 接口。
  - 异步任务对整图做一次性 AI 生成编辑，再逐人裁剪局部区域做质量复评；状态机 `QUEUED → LOCALIZING → REFINING → REVIEWING → REVIEW_REQUIRED → ACCEPTING → ACCEPTED`（另有 `CANCEL_REQUESTED`/`CANCELLED`/`FAILED`）。
  - 质量闸门（一致性/自然度/回归/目标问题改善/新增高危问题）+ 整图保护复评（保护物/其他人物/文字/构图四项 AND）全部通过才允许接受。
  - 接受接口带乐观锁（`expected_revision` + `candidate_digest`），不匹配返回 409。
  - 两阶段落盘：候选先落 `outputs/refinement_staging/`，接受后才写入 `outputs/api_results/refinement/accepted/`，未接受的候选不会进入正式产物目录。
  - 前端新增 `RefinementModal.tsx` 承载上述交互。

- **人像素材库 Portrait Library** (`api/portrait_library.py` + `api/portrait_library_import.py`)
  - SQLite 存储（`data/portrait_library/library.sqlite3`：`identities`/`assets`/`import_audit` 三表），素材文件分区存放，对外只暴露不透明 ID。
  - 新增 `GET /api/v1/portrait-library`、`GET /api/v1/portrait-assets/{asset_id}/thumbnail`、`GET /api/v1/portrait-assets/{asset_id}/content`。
  - 新增幂等导入 CLI `personaweave-import-portraits`，从 `manifest.csv` 批量导入外部素材，按内容哈希去重，损坏文件进 `quarantine/`。
  - 前端新增 `PortraitLibraryModal.tsx`，支持按性别/编号/年龄筛选并快速选取代表性素材注入 Source Identity Gallery。

- **换脸模型路由 (SwapperModelRouter)** (`core/model_router.py`)
  - 按人脸尺寸/姿态/检测置信度在 `inswapper_128`（默认）、`hyperswap_1c_256`、`ghost_1_256`（后两者需 `allow_research_models=true` 显式 opt-in，标注 "ResearchRAIL"）间路由，返回 `RoutingDecision(requested, selected, reason, fallback)`。

- **参考图质量评分**：`calculate_reference_quality()`/`describe_reference_quality()` 综合尺寸(35%)/清晰度(30%)/置信度(20%)/姿态(15%)给出 `excellent`/`usable`/`low` 三档，接入 `/api/v1/detect-faces` 响应。

- **跨图人脸聚类增强**：`/api/v1/cluster-faces` 在几何聚类后新增 `VLMIdentityArbiter.refine_clusters` 语义复核。

### ⚠️ 文档修正：以下 0.1.0 条目已退役/移除

旧版非生产 `swap_single_face_seamless()` 诊断变体不再保留为受支持能力。CodeFormer/GFPGAN 换脸后全脸修复、Poisson 合成、全脸色彩迁移和启发式发丝保护均已从当前能力声明中移除；生产路径始终为 `swap_single_face_facefusion()`，并继续执行下述身份与像素保护约束。

- "眼球视线与巩膜矫正"并非几何/像素矫正算法，实际仅是 VLM 精修审核 Prompt 中的一项自然度检查描述。
> Reinhard LAB 光影色温迁移与环状边缘残差色彩补偿已随下方"本地换脸修复"接入生产路径（仅边界环生效），不再属于此列表。

### 🐛 修复 (Fixes)

在同一次代码审查中定位并修复以下问题（均已补充测试，详见对应文件的测试覆盖）：

**AI 精修（已被整图直出架构取代的中间修复）**

- **[关键] `RefinementService.apply_candidate()` 缺失导致像素保护形同虚设**：`LocalizedRefinementEngine` 早已实现了带硬性断言（越界像素即抛异常）的 `align_generated()`/`composite()`/`apply_manual_protection()`，但 `api/refinement_task_manager.py` 从未调用它们——AI 提供方返回的整图候选被直接当作最终产物保存，背景、其他人物、整体色彩与饱和度完全没有代码层面的保护，只能靠 Prompt 请求模型"不要动"和事后的 VLM 抽查。现已新增 `RefinementService.apply_candidate()`，将整图候选通过每人的解剖学 mask 重新配准并合成回原图，只有 mask 支持区域会变化，其余像素逐字节保留。手动保护画笔（`manual_mask`）此前在精修流程中被静默忽略，现同样通过 `apply_manual_protection()` 生效。
- **VLM 诊断分辨率不足**：`RefinementService.review()` 此前只发送一张压缩到 1600px 的完整图片，合影中的单张脸可能只剩几十像素，难以做毛孔/瞳孔高光级别的取证判断。现为每个请求人物额外发送一张原分辨率局部特写。
- **精修重试无法升级模型**：新增 `RefinementRetryRequest.model_alias`（可选），失败或效果不理想时可对单个人物显式指定 `personaweave-refine-plus`/`personaweave-refine-max` 重试，Web Studio 精修弹窗提供对应下拉框；此前重试固定使用网关默认别名，无法触达配置中已有的更强模型。

**本地换脸（对照 FaceFusion 设计技巧修正）**

- **pixel-boost 放大误用 `INTER_AREA` 插值**：`swap_single_face_facefusion` 的对齐裁剪固定使用只适合缩小的 `INTER_AREA`，但 pixel-boost 对中小尺寸人脸是放大而非缩小——按 OpenCV 官方文档，`INTER_AREA` 放大时效果接近 `INTER_NEAREST`，会让送入换脸模型的输入偏糊。现按仿射变换的实际缩放比例动态选择插值方式。
- **合影多人映射为贪心分配，可能张冠李戴**：`process_image_multi_mapping` 此前按 `mappings` 列表顺序让每个映射独立抢占自己的最佳匹配人脸，先处理的映射可能抢走本该属于后一个映射的更强匹配（例如两个目标身份都最像同一张脸时）。新增 `resolve_person_mapping_assignment()`，用匈牙利算法求解全局最优一一分配，阈值过滤在优化前后各做一次以避免"被迫接受"低于阈值的配对。
- **XSeg 遮挡 mask 边缘羽化未随分辨率缩放**：`core/occluder.py` 固定使用 5px 高斯模糊，而 box mask 的羽化是按裁剪宽度比例计算的；大脸特写（pixel-boost 到 1024px）因此比小脸有更硬的遮挡边缘。现按裁剪宽度等比缩放羽化半径。
- **生产路径完全没有色彩校正**：`swap_single_face_facefusion` 此前对贴回前的换脸结果不做任何色彩处理，而 Reinhard 边界环校正代码只存在于未被调用的诊断路径里，源图与目标场景的曝光/白平衡差异会在脸部边缘留下可见色差。现将边界环校正（只修正 mask 边缘窄环，不改变脸部本体色调）接入生产路径，`enable_color_transfer`/`color_transfer_mode` 参数不再是无效占位。

**其他**

- `pyproject.toml`：`httpx` 从 `[dependency-groups].dev` 移入 `[project].dependencies`——`core/ai_gateway.py`、`core/providers/*.py` 在生产代码中依赖它，此前纯生产环境安装（不带 `--dev`）会导致 `ImportError`。

---

## [0.1.0] - 2026-08-25

### 🎉 首个完整版本发布

#### Phase 1: 核心算法与 CLI 离线管线 ✅

- **SCRFD-10G 人脸检测**：高精度 BBox + 5 关键点定位，支持多尺度多人场景。
- **ArcFace (w600k_r50) 身份特征提取**：512 维归一化向量，余弦相似度阈值匹配（默认 τ=0.35）。
- **InSwapper-128 核心换脸模型**：五官几何特征注入生成，支持仿射对齐裁剪与逆变换贴回。
- **CodeFormer + GFPGAN v1.4 双引擎面部超分辨率**：
  - CodeFormer 重点保留自然毛孔纹理（保真度权重 w=0.72）。
  - GFPGAN v1.4 增强眼神光、睫毛与五官锐利度。
  - Dual-Engine 模式以 50:50 加权融合两者优势。
- **Reinhard LAB 光影色温迁移**：均值/方差通道对齐，消除跨图肤色色差。
- **环状边缘残差色彩补偿**：分析内外椭圆过渡环 RGB 残差中位数，消除融合边缘色带。
- **3D Roll-Invariant Yaw 姿态匹配**：根据目标人脸偏航角自动选择最佳匹配源参考图。
- **多视角源身份图库 (Multi-View Gallery)**：支持 1~5 张多角度参考图加权融合。
- **CLI 批量处理脚本** (`cli.py`)：命令行一键批量换脸与质量验证。

#### Phase 2: FastAPI 服务化与异步任务管理 ✅

- **RESTful API 服务层** (`api/app.py`)：
  - `POST /api/v1/batch-tasks`：异步批量换脸任务提交。
  - `GET /api/v1/batch-tasks/{task_id}`：任务状态与进度轮询。
  - `POST /api/v1/swap-sync`：同步单图即时换脸。
  - `POST /api/v1/detect-faces`：人脸快速检测与姿态分析。
  - `POST /api/v1/cluster-faces`：跨图人脸身份聚类与全局 ID 分配。
  - `POST /api/v1/vlm/arbitrate`：多模态 VLM 智能身份仲裁。
- **Pydantic 数据契约** (`api/schemas.py`)：完整请求/响应类型定义。
- **异步批处理任务管理器** (`api/task_manager.py`)：线程池执行、实时进度追踪与 Webhook 回调。
- **自动化测试套件** (`tests/`)：API 健康检查、同步换脸、批处理异步与数学单元测试。

#### Phase 3: PersonaWeave Web Studio 前端工作台 ✅

- **技术栈**：Vite 6 + React 19 + TypeScript 5 + Tailwind CSS + shadcn/ui + Framer Motion。
- **源身份图库面板** (`SourceGallery`)：拖拽上传、3D Yaw 姿态识别与融合策略开关。
- **中央交互式画布** (`TargetCanvas`)：
  - 人脸选框交互标注（点击指定替换目标）。
  - 前后卷帘对比 (Split Slider) 模式。
  - 300% 局部微观放大镜 (Magnifier) 模式。
  - 跨图全局人物聚类 ID 标注。
  - AI VLM 大模型纠错按钮。
  - 底部胶片缩略图 (Filmstrip) 批量切换。
- **算法超参数调优面板** (`ParameterPanel`)：余弦阈值、保真度推子、引擎选择与即时试换。
- **批量流水线监控抽屉** (`BatchPipelineDrawer`)：进度条、耗时统计、Confetti 完成特效与 ZIP 一键打包。

#### Phase 3.5: 第一梯队高级增强功能 ✅

- **106 点稠密解剖学轮廓遮罩 (DenseFaceParser)** 🆕
  - 新增 `core/face_parser.py`，加载 `buffalo_l/2d106det.onnx` 模型。
  - 提取耳根至下颌尖完整 33 点连续下颌线、眉骨拱线与额头凸包轮廓。
  - 在 512×512 空间动态生成精准匹配面部骨骼的解剖学遮罩。
  - 解决传统椭圆遮罩在特殊脸型/尖下巴时的边缘漏底问题。
  - 失败时优雅降级为高精度椭圆默认遮罩。

- **画布交互式手动修复保护笔 (Manual Protection Brush)** 🆕
  - 在 `TargetCanvas.tsx` 画布视图新增 `🖌️ 保护笔 (BRUSH)` 模式。
  - 支持画笔 / 橡皮擦切换、10px~60px 粗细无级调节、一键清空遮罩。
  - 涂抹数据以 base64 编码 PNG 二值灰度图传递至后端 `manual_mask` 参数。
  - `pipeline.py` 中通过 `mask_warped = mask_warped * (1.0 - manual_mask)` 实现涂抹区域 100% 像素级原图保留。

- **合影多角色一键映射矩阵 (Multi-Person Matrix Mapping)** 🆕
  - 新增 `MultiPersonMatrixModal.tsx` 多角色映射卡片模态框。
  - 自动呈现合影中检测到的所有人物 (#1, #2, #3...)。
  - 用户可为每位人物分别绑定不同源身份图库。
  - 新增 `PersonMappingItem` API 数据模型与 `process_image_multi_mapping()` 后端方法。

- **眼球视线与巩膜聚焦矫正 (Eye Gaze & Sclera Harmonization)** 🆕
  - 解决正面源脸映射至侧倾目标时产生的"斗鸡眼"问题。
  - 以局部高斯遮罩保留原图 35% 眼神光与注视方向。

- **额头发丝与刘海保护 (Hair Strand & Bangs Protection)** 🆕
  - 亮度差值自动检测额头暗色发丝覆盖区域。
  - 通过软 Alpha 混合还原原始头发像素。

- **VLM 多模态智能仲裁器 (VLM Arbiter)** 🆕
  - 新增 `core/vlm_arbiter.py`，通过 LiteLLM 接入 Gemini / GPT-4V。
  - 在纯几何特征匹配失效时（大侧脸/背影），利用视觉大模型进行 Chain-of-Thought 推理辅助识别。

---

### 🏗️ 待实施功能 (Planned)

- **Phase 4**: TensorRT FP16/INT8 量化编译、多进程并发 Worker、Docker 全栈镜像打包。
- **视频换脸管线**：MP4/MOV 逐帧检测 + 光流时序平滑（Optical Flow Temporal Smoothing）。
- **BiSeNet 语义保护区域扩展**：当前仅默认保护 `glasses`；模型本身支持的耳饰、项链等其他类别尚未接入可配置的保护区域选择。
