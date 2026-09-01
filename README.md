# PersonaWeave

> **工业级批量图片定点高保真 AI 换脸系统**
> High-Fidelity Batch Identity Swapping & Harmonization System

---

## 📖 项目简介

**PersonaWeave** 是一套面向生产环境的批量图片 AI 换脸解决方案，专为满足复杂场景下的“定点人物替换”、“跨图身份一致性”以及局部换脸路径的非目标区域保真而设计。

系统基于 **FaceFusion 架构 + ONNXRuntime CPU/CUDA 推理** 方案进行二次开发。本地换脸使用受保护的局部仿射贴回，不重绘整张图；可选的 AI 精修则是明确区分的整图生成路径，可能改变背景、文字、人物或整体色彩，必须经过人工对比后才能接受。

---

## 🚀 核心能力现状 (Current Capabilities)

系统的**生产路径**（`core/pipeline.py` 中的 `process_image()` / `process_image_multi_mapping()`，即 API `/swap-sync`、`/batch-tasks` 与 CLI 默认调用的实现）目前包含：

1. **106点高密解剖学轮廓遮罩 (DenseFaceParser)**
   * 集成 `2d106det.onnx` 模型提取 106 点面部关键点（含下颌线、眉弓、额头凸包）。
   * 在 512×512 空间生成像素级解剖学 Mask，与 box mask、XSeg 遮挡 mask（`core/occluder.py`）、**BiSeNet 语义区域保护 mask**（`core/region_parser.py`，见下）取交集后仿射逆变换并以生产 alpha 合成贴回，杜绝边缘溢出。
   * 对齐裁剪的插值方式按仿射变换的实际缩放比例动态选择（放大用 `INTER_CUBIC`，缩小用 `INTER_AREA`），避免 pixel-boost 放大小/远景人脸时因误用 `INTER_AREA` 而糊边；XSeg 遮挡 mask 的羽化半径随裁剪分辨率等比缩放，避免大脸特写下遮挡边缘过硬。
2. **语义区域保护 (BiSeNet Region Parsing)**
   * `core/region_parser.py` 的 `RegionFaceParser` 加载 `models/bisenet_resnet_34.onnx`（19 类 CelebAMask-HQ 语义分割，vendor yakhyo，MIT 协议），对每张裁剪人脸做逐像素分类。
   * 生产路径默认保护 `glasses`（class 6）区域：XSeg 只识别"异物遮挡"（手、道具），不知道"这是眼镜"这个语义类别，换脸时眼镜镜框/镜片经常被模型融化变形；这里补上专门针对眼镜的保护。
   * 模型缺失时优雅降级为 `None`，不影响其余 mask 正常工作（与 XSeg/106点解析器同一套容错模式）。
3. **边界环形色彩校正 (Boundary-Ring Color Correction)**
   * `core/color_matcher.py` 的 Reinhard LAB 迁移现已接入生产路径，但只在换脸 mask 边缘的窄过渡环内生效（`swapped_crop` 本体色调不变），用于消除源参考图与目标场景之间的曝光/白平衡拼接缝，同时不影响整体色彩与饱和度。
4. **换脸模型路由 (SwapperModelRouter)**
   * 自动模式稳定使用 `inswapper_128`；只有显式启用 `allow_research_models=true` 且人脸尺寸、姿态与置信度满足门槛时才自动选择 `hyperswap_1c_256`。`ghost_1_256` 仅供显式实验选择，不进入自动路由。每次选择都会返回路由原因与是否触发降级。
5. **画布交互式手动保护画笔 (Manual Protection Brush Tool)**
   * Web Studio 引入画笔/橡皮擦工具（大小 10px~60px 可调），支持在目标图像上直接涂抹保护区域（如珠宝首饰、眼镜、胎记、复杂头饰等）。
   * 掩码数据基于 Base64 编码导出，在贴回前与解剖学 mask 相乘，100% 还原受保护区域的原始像素。
6. **合影多角色一键映射矩阵 (Multi-Person Matrix Mapping)**
   * 提供 👥 矩阵映射交互，自动展示合影中的所有被检测人物（#1, #2, #3...）。
   * 允许用户为每一个被检测到的人物独立分配不同的 Source Identity Gallery；请求体 `person_mappings` 字段非空时，后端自动路由至 `process_image_multi_mapping()`。
   * 人物-身份分配通过匈牙利算法（`resolve_person_mapping_assignment`）求解全局最优一一匹配，而非按列表顺序贪心抢占，避免相似长相人物在合影中被张冠李戴。
7. **跨图片人物聚类 + VLM 语义精修 (Cross-image Person Clustering)**
   * `/api/v1/cluster-faces` 先做几何特征聚类，再调用 `VLMIdentityArbiter.refine_clusters` 对聚类结果做一次语义复核。
8. **参考图质量评分 (Reference Quality Scoring)**
   * `/api/v1/detect-faces` 返回 `quality_score`/`quality_tier`（`excellent`/`usable`/`low`），综合尺寸、清晰度、检测置信度与姿态角度评估源图可用性。
9. **持久化任务历史与结果回看**
   * `GET /api/v1/task-history/batch-tasks` 返回轻量任务摘要；已完成/部分完成任务可按需加载完整详情，在只读对话框中切换多张结果、前后卷帘、缩放、打开或下载，不会覆盖当前工作台。旧 `GET /api/v1/batch-tasks` 完整列表为兼容性保留接口。
   * 新任务同时持久化原始画布与处理结果，浏览器只保存任务关联元数据。权威契约见 [docs/task-history.md](./docs/task-history.md)。

### ⚠️ 已退役的非生产换脸实验

旧版 `swap_single_face_seamless()` 诊断/研究变体已退役，不再作为受支持能力或可用路径记录。生产换脸只以 `swap_single_face_facefusion()` 为准；CodeFormer/GFPGAN 换脸后全脸修复、Poisson 合成、全脸色彩迁移及启发式发丝保护均不属于生产功能，也不得通过参数重新启用。
"眼球视线与巩膜矫正"也不是几何/像素级矫正算法，而只是 VLM 精修审核 Prompt 里的一项检查项（判断画面 gaze 是否自然）。

生产换脸算法的权威、准确描述见 [docs/facefusion-local-swap.md](./docs/facefusion-local-swap.md)。工程依赖方向、生产/诊断代码分类、兼容与删除规则见 [docs/engineering-architecture.md](./docs/engineering-architecture.md)。

### 🆕 AI 精修与素材管理

* **AI Gateway**（`core/ai_gateway.py` + `config/ai_gateway.yaml`）：配置驱动的 LiteLLM Router 封装，统一管理 VLM（`personaweave-vision-*`）与图像编辑（`personaweave-refine-*`）模型别名，均通过 [DMXAPI](https://www.dmxapi.cn)（OpenAI 兼容协议的第三方模型聚合网关，代理 Qwen-VL / Gemini / Doubao-Seedream / Qwen-Image-Edit 等模型）调用；`core/providers/` 下的 `dmx_seedream.py`/`dmx_responses.py` 是针对 DMX 私有响应协议的自研 httpx 适配器。`core/vlm_arbiter.py` 现在经由 `AIGateway` 调用，而非直接使用 LiteLLM。
  * 当前精确的 VLM、图像编辑模型、默认项和回退链见 [docs/model-catalog.md](./docs/model-catalog.md)；前端从 `GET /api/v1/ai/models` 动态读取非敏感图像模型目录，不维护另一份可用性清单。
* **整图 AI 精修**（权威设计见 [docs/ai-refinement.md](./docs/ai-refinement.md)）：先预检用户选择与人物定位，再用一次整图 VLM 调用整理每人的具体瑕疵和文字可见定位；用户可补充指令后，图像模型只接收当前有瑕疵的完整图像及简洁文字要求，不接收身份参考图、人物裁剪、mask 或关键点图。provider 的整图结果经规范化后直接保留为 staging candidate，不执行 `apply_candidate`、关键点对齐、mask 或局部合成；随后一次整图 VLM 复评只提供建议，由用户决定接受、重试或取消。
  * 流程保留 staging/accepted 两阶段落盘、`expected_revision` + `candidate_digest` 乐观锁、取消/重试/恢复、安全图片加载与原子发布。重试可选择配置的 `personaweave-refine-*` 模型别名。
* **人像素材库 Portrait Library**（`api/portrait_library.py`，SQLite 存储于 `data/portrait_library/library.sqlite3`；`api/portrait_library_import.py` 提供 `personaweave-import-portraits` 幂等导入 CLI，从 `manifest.csv` 导入外部素材）：Web Studio 内置素材库供快速挑选源身份参考图，对外只暴露不透明 ID，不泄露真实文件路径。

---

## 🎯 核心特性与设计目标

1. **指定人物定点换脸 (Targeted Face Swapping)**
   * 基于 ArcFace 512 维特征向量余弦相似度匹配，精准替换指定目标人物。
   * **Roll-Invariant Yaw-Ratio Matching**：基于 5 点关键点的偏航比例估计，自适应筛选接近目标朝向的源图片；该值不是 3D 头姿角。
2. **跨批次人物一致性 (Multi-Image ID Consistency)**
   * 支持多视角参考图建立“标准身份库（Identity Gallery）”。通过特征均值归一化，确保批量换出结果特征严格统一。
3. **背景与光影极高保真 (Zero Hallucination & Harmonization)**
   * **局部贴回**：仅提取人脸局部区域对齐，通过逆仿射变换贴回；最终 warped alpha 支持范围以外的像素保持原图。
   * **遮挡与边缘保护**：解剖学高密轮廓 + XSeg 遮挡 mask + 手动画笔三者取交集，杜绝边缘违和感。
   * **色彩补偿**：生产路径只执行 Reinhard LAB 边界环校正（仅修正 mask 边缘窄环，不改变换脸本体色调与整体饱和度）；已退役的全脸修复与全脸迁色不属于可选模式。
4. **极低资源消耗与高吞吐**
   * 运行时占用显存小，单张消费级显卡即可支撑生产级高并发，深度适配硬件加速。
5. **现代化交互前端 (PersonaWeave Web Studio)**
   * 采用 React 19 + Vite 6 + TypeScript + Tailwind CSS + Radix UI Primitives 构建的现代化生产控制台（未引入 shadcn/ui 生成式组件工作流，业务组件基于裸 Radix Primitives 手写 Tailwind 样式）。

---

## 📂 目录结构 (Directory Structure)

```text
PersonaWeave/
├── README.md
├── DESIGN.md
├── AGENTS.md
├── CHANGELOG.md
├── pyproject.toml
├── cli.py                    # 离线单目录/单人模式批处理（不含人像库/AI精修等新能力）
├── config/
│   └── ai_gateway.yaml       # AI Gateway 模型别名配置（VLM + 图像编辑）
├── core/                     # 核心算法与流水线模块
│   ├── __init__.py
│   ├── detector.py           # SCRFD 人脸检测与 5 点定位
│   ├── face_metrics.py       # Yaw、参考质量、可见变化与侧脸兼容纯规则
│   ├── recognizer.py         # ArcFace 身份特征提取与匹配
│   ├── swapper.py            # InSwapper-128 ONNX 推理封装
│   ├── model_router.py       # SwapperModelRouter：按人脸条件路由 inswapper/hyperswap/ghost
│   ├── color_matcher.py      # Reinhard LAB 色彩迁移（生产换脸仅使用边界环校正）
│   ├── occluder.py           # XSeg 遮挡区域检测
│   ├── region_parser.py      # BiSeNet 语义区域保护 (RegionFaceParser，默认保护眼镜)
│   ├── face_parser.py        # 106点高密面部解剖学轮廓遮罩 (DenseFaceParser)
│   ├── ai_gateway.py         # AI Gateway：LiteLLM Router 封装，统一管理 VLM/图像编辑模型别名
│   ├── vlm_arbiter.py        # VLM 多模态身份判定与仲裁（经 AIGateway 调用）
│   ├── vlm_contracts.py      # VLM 结构化输出的 Pydantic 契约
│   ├── refinement_service.py    # 整图预检/诊断、生成规范化与提示性整图复评服务
│   ├── providers/            # DMXAPI 私有协议适配器 (dmx_seedream.py / dmx_responses.py)
│   └── pipeline.py           # 完整处理流水线控制器 (PersonaWeavePipeline)
├── api/                      # RESTful API 服务层 (FastAPI)
│   ├── __init__.py
│   ├── app.py                # FastAPI 路由与生命周期
│   ├── schemas.py            # Pydantic 请求/响应模型
│   ├── task_manager.py       # 异步批量换脸任务管理器
│   ├── refinement_task_manager.py  # AI 精修专属异步任务管理器（草稿/正式两阶段落盘）
│   ├── portrait_library.py         # 人像素材库服务（SQLite）
│   └── portrait_library_import.py  # 幂等素材导入 CLI (personaweave-import-portraits)
├── web/                      # PersonaWeave Web Studio 前端 (pnpm)
│   └── src/
│       ├── components/
│       │   ├── ui/           # 对比查看器、Toast 与错误边界等共享组件
│       │   ├── studio/
│       │   │   ├── SourceGallery.tsx
│       │   │   ├── TargetCanvas.tsx       # ★ 包含手动保护画笔组件
│       │   │   ├── ParameterPanel.tsx
│       │   │   ├── BatchPipelineDrawer.tsx
│       │   │   ├── MultiPersonMatrixModal.tsx  # ★ 合影多角色一键映射
│       │   │   ├── RefinementModal.tsx         # ★ 整图 AI 精修编排交互
│       │   │   ├── RefinementReviewPanels.tsx  # 精修纯展示面板
│       │   │   └── PortraitLibraryModal.tsx    # ★ 人像素材库选取
│       │   └── layout/       # AppHeader 等
│       ├── hooks/            # 跨组件行为 Hook（聚类、保护画笔）
│       ├── stores/           # Zustand 全局状态管理 (useStudioStore)
│       ├── services/         # API Client (api.ts)
│       ├── types/            # 共享类型定义
│       └── utils/            # cn.ts、sourceReferences.ts 等工具函数
├── utils/
│   ├── affine.py             # 仿射变换工具
│   └── gpu_helper.py         # 硬件加速辅助
├── models/                   # ONNX 模型资产
├── tests/
├── scripts/
├── data/                     # 含 data/portrait_library/（人像库 SQLite + 素材文件）
└── outputs/                  # 含 outputs/refinement_staging/（AI精修候选草稿区）
```

> 说明：项目前端未引入 shadcn/ui 生成式组件工作流；`components/ui/` 存放项目自身的共享 UI，业务组件基于 Radix UI Primitives 与 Tailwind 组合。

---
## 🛠️ 完整开发环境配置与启动

### 1. 前置条件与依赖

需手动安装 Python 3.11+、uv、Node.js 和 pnpm。GPU 为推荐配置；CPU Provider 也可运行但较慢。启动脚本不会自动安装、下载或写环境文件。

```bash
uv sync --dev
pnpm --dir web install
```

### 2. 模型与环境

启动预检当前要求：`models/buffalo_l/det_10g.onnx`、`models/buffalo_l/w600k_r50.onnx`、`models/buffalo_l/2d106det.onnx`、`models/inswapper_128.onnx`。其中 `2d106det.onnx` 服务于本地换脸的 106 点解剖学遮罩；单次关键点提取失败会按生产约定降级为椭圆遮罩。可选的 `models/xseg_1.onnx`（异物遮挡检测）与 `models/bisenet_resnet_34.onnx`（语义区域保护，见 [models/README.md](./models/README.md)）缺失时会优雅降级，不阻断其余本地 mask。下载器会校验每个受管资产的 SHA-256。可手动放置，或明确运行：

```bash
uv run python scripts/download_models.py
cp .env.example .env
```

在本地 `.env` 填写 `DMXAPI_API_KEY` 和 `DMXAPI_API_BASE`（默认 `https://www.dmxapi.cn/v1`），不要提交真实密钥。[DMXAPI](https://www.dmxapi.cn) 是一个 OpenAI 兼容协议的第三方模型聚合网关，代理 Qwen-VL、Gemini、Doubao-Seedream、Qwen-Image-Edit 等模型，供 `core/ai_gateway.py` 统一调用。`PERSONAWEAVE_AI_CONFIG` 指向 AI 网关 YAML（默认 `config/ai_gateway.yaml`）；开发 CORS 来源为 `http://localhost:3000`。启动器会检查 YAML 中全部 `os.environ/NAME` 引用。

### 3. 一键启动（推荐）

```bash
uv run python scripts/dev.py
```

脚本预检 uv/pnpm、必需模型、8000/3000 端口、ONNX CPU/CUDA Provider 和 AI 环境变量。通过后启动 API <http://localhost:8000>（文档 <http://localhost:8000/docs>）与 Web <http://localhost:3000>。两者日志直接输出到当前终端；按 `Ctrl+C` 会优雅停止两个进程，任一服务意外退出也会停止另一个。

### 4. 手动启动与排障

在两个终端运行：

```bash
uv run uvicorn api.app:app --host 0.0.0.0 --port 8000
pnpm --dir web dev --host 0.0.0.0 --port 3000 --strictPort
```

预检失败时确认端口未占用、模型路径准确，并运行 `uv run python -c "import onnxruntime as o; print(o.get_available_providers())"`。CPU Provider 可用；CUDA 缺失仅警告。若预期 GPU，请检查驱动、CUDA/cuDNN wheel 与 ONNX Runtime 兼容性。

## ✅ AI 整图精修验收指南

> 权威流程与契约见 [docs/ai-refinement.md](./docs/ai-refinement.md)。精修以“当前有瑕疵的整图 + 文字人物定位/瑕疵”为唯一图像生成输入，provider 整图结果规范化后直接进入 staging；不存在 `apply_candidate`、关键点对齐、mask 或局部合成。

1. 打开 Web Studio，在当前图像中选择一个或多个人物；预检必须确认 revision 有效并能可靠定位所有选择。
2. 用一次整图 VLM 分析整理每人的具体瑕疵与简洁可见定位。检查结构化结果，并按需补充、删除或修正用户指令后再明确启动生成。
3. 提交任务后确认图像模型只收到当前有瑕疵的完整图像这一张图片，以及文字定位、完整瑕疵清单和用户补充；不得发送身份参考图、素材库图、人物裁剪、mask、关键点图或旧 revision。
4. provider 返回的整图只做编码、方向、色彩模式、尺寸和元数据规范化，随后将该整图直接写入 `outputs/refinement_staging/{task_id}/`。失败或取消不得产生正式产物。
5. 对规范化候选执行一次整图 VLM 质量复评，检查目标问题改善、新问题和无关整图变化。复评仅供参考，不自动通过或阻断接受；用户预览后自行决定接受、重试、基于候选继续精修或取消。
6. 接受时必须携带 `expected_revision` + `candidate_digest`；不匹配返回 409。系统原子发布与该 digest 对应的 staging 字节到 `outputs/api_results/refinement/accepted/`，不得重新生成、重新编码或重新合成。取消、重试和恢复还须保持 revision/digest 与 staging 隔离，并使用安全图片加载。

## 📚 人像素材库 (Portrait Library)

* 后端存储：SQLite（`data/portrait_library/library.sqlite3`），三张表 `identities`/`assets`/`import_audit`；素材文件分区存放于 `data/portrait_library/{originals,thumbnails,quarantine}/`，对外只暴露不透明 ID（内容哈希前20位），从不返回真实文件路径。
* 接口：`GET /api/v1/portrait-library`（按身份分组的素材目录）、`GET /api/v1/portrait-assets/{asset_id}/thumbnail`、`GET /api/v1/portrait-assets/{asset_id}/content`。
* 导入外部素材：使用幂等导入 CLI（安装后提供 `personaweave-import-portraits` 命令，对应 `api/portrait_library_import.py`），从 `manifest.csv`（Windows 风格相对路径）批量导入，按内容哈希跳过未变化文件，损坏图片自动移入 `quarantine/`。
* 前端 `PortraitLibraryModal.tsx`：内置素材库供按性别/编号/年龄筛选身份；默认最多选择 8 张，并按正脸、左右大侧脸、左右低头侧脸和接近 ±30° 的角度确定性分层覆盖，不再随机抽样。极端侧脸若缺少同方向参考，后端会跳过该人物并在前端显示部分成功警告，避免输出双眼/鼻部畸变。
* 多排合影的位置文案由 `faceTopology.ts` 按行聚类后显示为“后排/前排·从左到右第 N 位”；稳定的 `person_index` 仍保持 API 约定的全局从左到右编号，仅展示文案分行，不改变后端人物映射。

## 📦 模型与测试资产

大型 ONNX 权重不进入普通 Git。模型目录、可选资产及许可说明见 [models/README.md](./models/README.md)。API 集成测试只使用 [tests/fixtures](./tests/fixtures) 中的最小图片集；`data/` 本地素材默认忽略。

## 📚 详细设计文档

* [DESIGN.md](./DESIGN.md)：系统架构、算法和 API 契约。
* [docs/ai-refinement.md](./docs/ai-refinement.md)：整图 AI 精修权威流程。
* [docs/facefusion-local-swap.md](./docs/facefusion-local-swap.md)：本地换脸生产路径。
* [docs/model-catalog.md](./docs/model-catalog.md)：当前 VLM/图像编辑模型、默认项和回退链。
* [docs/task-history.md](./docs/task-history.md)：批量任务历史、持久化结果与只读回看契约。
* [docs/documentation-governance.md](./docs/documentation-governance.md)：文档事实源、证据矩阵和同步门槛。
