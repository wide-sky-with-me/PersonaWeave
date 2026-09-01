# Agent Guidelines (AGENTS.md)

本文件规定了 AI Agent 在本代码库/项目中进行开发、维护和执行任务时必须严格遵守的行为准则与操作规范。

---

## 1. 核心设计原则 (Core Principles)

### 1.1 遵守 KISS 原则，拒绝过度设计 (Keep It Simple, Stupid)
- **实用优先**：优先采用最直接、简单且健壮的方案解决问题，坚决避免过早优化和不必要的抽象层（如滥用设计模式、过度解耦、多层包装）。
- **按需编码**：仅实现当前明确需要的功能，不预先编写“未来可能用到”的未决逻辑（YAGNI 原则 - You Aren't Gonna Need It）。
- **直观易读**：代码逻辑应当自解释，结构扁平清晰，避免晦涩难懂的奇技淫巧。

### 1.2 代码注释与文档规范 (Code Commenting)
- **关键位置必须写好注释**：
  - **核心业务逻辑与算法**：复杂计算、状态流转、非平凡算法分支需附带逻辑解释。
  - **非显而易见的决策 (Why)**：解释为什么采用某种特殊解法、规避了什么已知 Bug 或系统限制。
  - **接口与函数说明**：公共函数/类提供清晰的 Docstring，标明核心入参、返回值及可能的异常。
  - **规避冗余注释**：避免对显而易见的代码进行“废话式”直译注释（例如 `i = i + 1  # i加1`）。

---

## 2. 环境与包管理规范 (`uv`)

本项目强制使用 **[`uv`](https://github.com/astral-sh/uv)** 作为 Python 环境与依赖管理工具，严禁随意使用原生 `pip` 污染全局或系统环境。

### 2.1 常用工作流命令
- **虚拟环境管理**：
  ```bash
  # 创建虚拟环境
  uv venv

  # 激活虚拟环境（Linux/macOS）
  source .venv/bin/activate
  ```
- **依赖管理**：
  ```bash
  # 安装/添加依赖
  uv add <package_name>
  # 添加开发依赖
  uv add --dev <package_name>
  # 依据 pyproject.toml / requirements.txt 同步依赖
  uv sync
  # 或使用 uv pip 兼容命令
  uv pip install -r requirements.txt
  ```
- **运行命令与脚本**：
  ```bash
  # 在虚拟环境中执行命令
  uv run <command>
  # 运行指定 Python 脚本
  uv run python main.py
  # 运行测试
  uv run pytest
  ```

---

## 3. 代码风格、格式化与规范检查 (`ruff`)

本项目强制使用 **[`ruff`](https://github.com/astral-sh/ruff)** 作为 Python 代码的统一格式化器 (Formatter) 与静态规范检查器 (Linter)。

### 3.1 核心命令与工作流
所有代码编写或修改完成后，**必须依次执行格式化与规范检查，确保 0 报错、0 警告**：

- **代码格式化**：
  ```bash
  # 自动格式化项目所有 Python 文件
  uv run ruff format .

  # 仅检查代码格式是否合规（不修改文件，CI/前置校验常用）
  uv run ruff format --check .
  ```

- **规范检查与静态分析 (Linting)**：
  ```bash
  # 检查代码中的规范问题、未使用的导入/变量、潜在 Bug
  uv run ruff check .

  # 自动修复所有可安全自动修复的规范问题（如导入排序、废弃语法等）
  uv run ruff check --fix .
  ```

### 3.2 规范与编码准则
1. **导入语句 (Imports)**：
   - 严禁残留未使用的 `import`。
   - 遵循 `isort` 规则自动分组排序（标准库 -> 第三方库 -> 本地模块）。
2. **变量与代码清理**：
   - 严禁残留未使用的局部变量（若必要请使用 `_` 或 `_var` 显式命名）。
   - 避免无意义的盲目捕获 `except Exception: pass`，如有特定目的应显式捕获具体异常或添加说明。
3. **一致性风格**：
   - 字符串引号统一采用双引号 `"`（由 Ruff Formatter 自动维护）。
   - 单行最大字符宽度遵循 `pyproject.toml` 配置（120 字符）。

---

## 4. 依赖安装与系统前置检查 (Pre-installation & Compatibility Check)

在引入新依赖、安装系统组件或升级软件包之前，**必须严格执行“先检查、再评估、后安装”**的步骤。

### 4.1 第一步：检查系统环境与现有状态
在执行任何安装操作前，先运行诊断命令确认当前运行环境：
- **操作系统与硬件架构**：`uname -a`, `cat /etc/os-release`（确认是 Linux/macOS/Windows 以及 x86_64/ARM64）。
- **Python / 运行时版本**：`uv run python --version` 或当前活跃版本。
- **现有已安装库**：检查 `pyproject.toml`、`uv.lock` 或 `uv pip list`，避免重复安装或引起依赖版本颠簸（Dependency Thrashing）。

### 4.2 第二步：必要性评估 (Necessity Assessment)
- **优先使用标准库/现有依赖**：评估该功能是否可以通过 Python 标准库（如 `pathlib`, `json`, `dataclasses`, `functools`, `typing` 等）或已有依赖轻量实现。
- **避免臃肿库**：严禁为了微小功能引入庞大的全家桶库（例如仅为了简单求均值而引入大型复杂依赖）。

### 4.3 第三步：兼容性与安全性验证 (Compatibility & Safety Verification)
- **依赖冲突检查**：确认待安装包与已有核心依赖的版本兼容性范围。
- **平台支持**：确认目标包是否支持当前的操作系统及架构（避免缺少编译环境导致 C 扩展构建失败）。
- **安装后验证**：安装后必须立即运行一次冒烟测试或轻量验证脚本，确认依赖能够正常导入并正常运行：
  ```bash
  uv run python -c "import <package_name>; print(<package_name>.__version__)"
  ```

---

## 5. 前端工程与工作台开发规范 (`pnpm`)

本项目的前端工作台（Web Studio）强制使用 **[`pnpm`](https://pnpm.io/)** 作为唯一的包管理器，严禁随意使用 `npm` 或 `yarn` 导致 lockfile 混乱与幽灵依赖问题。

### 5.1 常用前端工作流命令
- **依赖安装与更新**：
  ```bash
  # 安装项目所有依赖
  pnpm install

  # 添加生产依赖
  pnpm add <package_name>

  # 添加开发依赖
  pnpm add -D <package_name>
  ```
- **开发、构建与检查**：
  ```bash
  # 启动本地热重载开发服务器
  pnpm dev

  # 执行代码规范与静态分析
  pnpm lint

  # 执行生产构建
  pnpm build
  ```

### 5.2 前端代码风格、组件设计与动效规范
1. **组件化与 KISS 原则**：
   - 遵循单向数据流与清晰职责边界，严禁将所有状态塞入单一臃肿组件。
   - 基础原子组件优先选用基于 **shadcn/ui + Radix UI Primitives** 的无头封装，业务组件保持自解释与纯粹。
2. **UI/UX 现代科技美学规范 (Figma 级高质感)**：
   - **深色模式与层次感 (Dark Cyber-Aesthetics)**：采用深灰黑底色（如 `#09090b`, `#18181b`）、细微半透明边框（`border-white/10`）与精细磨砂玻璃模糊（`backdrop-blur-md`）。
   - **数据与交互反馈**：人脸识别框、置信度标签、相似度热力图与对比滑块需具备清晰高亮态（Primary Accent Color 如 Electric Indigo `#6366f1` 或 Neon Cyan `#06b6d4`）。
   - **物理微动效 (Micro-Interactions)**：交互过渡统一使用 **Framer Motion** 的弹簧物理曲线（`type: "spring", stiffness: 300, damping: 25`），避免僵硬生硬的线性突变。
3. **状态与数据流控制**：
   - 全局客户端状态采用 **Zustand**，异步服务端接口交互与状态轮询采用 **TanStack Query (React Query)**。
   - 严格处理图片与 Blob 的内存回收（及时调用 `URL.revokeObjectURL` 避免内存泄漏）。

---

## 6. 执行与验证规范 (Execution & Quality Checklist)

AI Agent 在完成代码编写或修改后，必须遵守以下质量清单：

- [ ] **Python 后端代码规范 (Ruff)**：是否执行了 `uv run ruff format .` 与 `uv run ruff check .` 并达到 0 警告 / 0 报错？
- [ ] **前端代码规范 (pnpm)**：是否严格使用 `pnpm` 管理依赖，且组件结构扁平清晰无冗余？
- [ ] **代码简洁性**：是否遵循 KISS 原则？是否存在冗余类/函数/包装层？
- [ ] **注释完整性**：核心逻辑、边界情况、非直观设计是否有清晰注释？
- [ ] **环境隔离**：所有 Python 执行通过 `uv`，前端执行通过 `pnpm`？
- [ ] **运行验证**：修改后是否运行了测试用例 (`uv run pytest` / `pnpm test`) 或执行了功能验证？
- [ ] **环境整洁**：未留下未清理的临时测试文件或垃圾代码。

---

## 7. 新增功能模块开发规范 (New Feature Module Conventions)

### 7.1 面部解析与密集关键点模块 (Dense Face Parser)
- `core/face_parser.py` 中的 `DenseFaceParser` 类负责 106 点面部关键点提取与解剖学轮廓掩码生成。
- 关键点索引约定：0-32 为下颌线，33-42 为左眉，43-52 为右眉，53-71 为鼻部，72-92 为嘴部，93-96 与 97-105 分别为左右眼轮廓。
- 掩码生成必须在 512×512 对齐空间中完成，并使用 31×31 高斯模糊进行边缘羽化。
- 当关键点提取失败时，必须优雅降级为椭圆默认掩码，禁止抛出异常中断流水线。

### 7.2 手动保护画笔交互规范 (Manual Protection Brush)
- 前端保护画笔相关状态统一管理在 `useStudioStore` 的 Zustand store 中。
- 画笔遮罩数据以 base64 编码的 PNG 灰度图传递至后端 `manual_mask` 参数。
- 后端在 `pipeline.py` 中通过 `mask_warped = mask_warped * (1.0 - manual_mask)` 实现保护区域像素回填。
- 遮罩值约定：白色 (255) = 保护区域（保留原图），黑色 (0) = 允许换脸区域。

### 7.3 多角色映射矩阵 (Multi-Person Matrix Mapping)
- API 层通过 `PersonMappingItem` 模型定义单个人物映射关系：稳定的 `person_index`、代表图上的 `target_reference_url` + `target_reference_person_index`，以及三选一的 `source_identity_id` / `source_asset_ids` / `source_manual_images` 和 `source_mode`。
- `person_mappings` 多角色模式与请求顶层的单角色 source/target 字段互斥；`api/schemas.py` 会拒绝混用，禁止恢复“非空时静默优先”的旧行为。
- 当 `person_mappings` 非空时，后端自动路由至 `process_image_multi_mapping()` 方法。
- 人物编号 (`person_index`) 始终为 **1-based**，按合影中人脸从左至右排序。
- 人物-人脸的分配必须通过 `resolve_person_mapping_assignment()`（`core/pipeline.py`，匈牙利算法全局最优解）完成，**禁止**改回按列表顺序逐个贪心抢占最佳匹配——贪心分配会让排序靠前的映射抢走本该属于后一个映射的更强匹配。新增筛选/排序逻辑时必须保留"低于阈值的配对在优化前屏蔽、优化后用真实相似度再校验"的双重保护。

### 7.4 VLM 多模态仲裁器 (VLM Arbiter)
- `core/vlm_arbiter.py` 通过 `core.ai_gateway.AIGateway`（LiteLLM Router 封装，见 7.5）接入视觉大模型，**不再直接调用 LiteLLM**。
- API 密钥通过环境变量 `.env` 配置，禁止在代码中硬编码。
- VLM 仲裁结果仅作为辅助参考，最终身份匹配仍以 ArcFace 余弦相似度为准。

### 7.5 AI Gateway 配置规范 (`core/ai_gateway.py`)
- 所有 VLM/图像编辑模型访问必须经由 `AIGateway` 统一网关，禁止在业务代码中直接实例化 LiteLLM Router 或裸调用第三方 SDK。
- 模型别名（VLM: `personaweave-vision-*`；图像编辑: `personaweave-refine-*`）统一在 `config/ai_gateway.yaml` 中声明；新增别名或 provider 时同步更新该文件，路径可由环境变量 `PERSONAWEAVE_AI_CONFIG` 覆盖。
- VLM 业务用途必须通过 YAML 的 `vision_roles` 和 `AIGateway.vision_alias()` 解析，业务模块不得硬编码具体 VLM 别名。图像模型的分辨率/网格/像素上限必须写入对应 `image_models[].generation`，禁止在业务代码里按 alias 字符串猜测模型能力；网关加载时必须拒绝缺失或无效能力声明。
- YAML 中所有密钥/base URL 必须使用 `os.environ/NAME` 语法从环境变量解析，禁止在 YAML 或代码中硬编码明文密钥。
- 新增 DMX 私有协议（非标准 OpenAI/LiteLLM 格式）的 provider 时，参照 `core/providers/dmx_responses.py` 的 httpx 适配器模式，统一返回 `ImageEditResult(image_bytes, model, provider, usage, request_id)`。
- VLM 结构化输出必须走 `core/vlm_contracts.py` 中定义的 Pydantic 契约做"契约优先"校验，禁止直接解析裸 JSON/字符串。
- 涉及"瑕疵点/issue"类字段（`ReviewIssue.category/region/description/suggestion`、`WholeImageQualityReview.issues`）必须保持具体、可验证：`category` 只能是 `core/vlm_contracts.py::ReviewIssue` 中已核实的七分类枚举值，禁止改回自由字符串；`region`/`description` 的最短长度约束与 Prompt 里的"不合格 vs 合格"few-shot 范例必须一起改，不要只改其中一处——放宽 Prompt 措辞而不同步收紧契约（或反过来）会让"瑕疵点不够具体"的问题重新出现。新增/修改这类 Prompt 时需跑通 `tests/test_vlm_contracts.py` 与 `tests/test_refinement_service.py` 中对应的具体性断言。

### 7.6 整图 AI 精修任务规范 (Whole-image AI Refinement)
- `docs/ai-refinement.md` 是 AI 精修架构与契约的权威文档；其他说明与之冲突时以该文档为准。
- 生成前必须完成用户人物选择与定位预检，并用**一次整图 VLM 调用**为所有选中人物组织具体、可验证的瑕疵清单和简洁的文字可见定位；结果须经结构化契约校验，并允许用户补充或修正指令后再生成。
- 图像模型每次只允许收到**当前有瑕疵的完整图像这一张图片**，以及选中人物的文字可见定位、完整瑕疵清单和用户补充指令。禁止附带身份参考图、素材库图片、人物裁剪、mask、关键点图、标注图或旧 revision 图片；`face_index` 只允许用于前端人物关联和无 VLM 定位时生成简单的文字回退，不得转成坐标或标注图传给生图模型。
- provider 返回的整图结果只做格式、方向、色彩模式、尺寸与元数据规范化；规范化后的整图必须直接作为 staging candidate 保留。精修路径禁止调用 `apply_candidate()`，禁止关键点重新配准、mask 应用、手动保护合成及任何局部像素合成。
- 生成后必须用**一次整图 VLM 调用**对规范化候选做质量复评；复评失败时必须保留已经生成并规范化的 staging candidate，将失败记录为提示性 `review_error`，不能把生成成功的候选改判为任务失败。复评结果或失败信息均只作为提示性证据展示，不能成为自动接受/拒绝的强制闸门；最终是否接受只由用户明确决定。
- 精修任务必须遵守两阶段落盘：未接受候选只写入 `outputs/refinement_staging/{task_id}/`；只有显式接受时才可原子发布到 `outputs/api_results/refinement/accepted/`，失败、取消和复评均不得产生部分正式产物。
- 接受接口必须校验 `expected_revision` + `candidate_digest` 乐观锁；不匹配返回 409。发布的必须是该 digest 对应的规范化 staging 字节，不得重新生成、重新合成或换一个编码版本。
- 状态语义遵循 `QUEUED → REFINING → REVIEWING → REVIEW_REQUIRED → ACCEPTING → ACCEPTED`，并保留 `CANCEL_REQUESTED`/`CANCELLED`/`FAILED`。人物选择、VLM 诊断和人工补充发生在创建生成任务之前；迁移期可识别旧持久化任务中的 `LOCALIZING`，但新任务不得再发出该状态。
- 取消、重试和恢复必须幂等，保留 revision/digest、staging 隔离、模型别名与非敏感故障诊断；不得静默切换当前输入 revision。用户明确选择“基于候选继续精修”时，该候选才成为下一轮唯一的当前有瑕疵整图。
- 图片加载必须使用受控且安全的解析路径，执行来源允许列表、路径/URL 防护、大小限制与解码校验；禁止直接信任用户或模型提供的文件路径。
- 所有 VLM/图像模型仍须经 `AIGateway` 和配置别名路由。新增 provider/别名时同步核实整图分辨率与响应规范化能力，且不得在适配器中暗中加入参考图或局部处理。

### 7.7 人像素材库规范 (Portrait Library)
- 素材库持久化统一使用 SQLite（`data/portrait_library/library.sqlite3`），禁止改为直接文件系统扫描或引入额外数据库依赖。
- 对外 API 只能暴露不透明 ID（内容哈希前20位），禁止在响应中泄露服务器真实文件路径；新增文件访问接口时必须做 `is_relative_to` 防路径穿越校验。
- 新增导入来源时优先扩展 `api/portrait_library_import.py` 的 `manifest.csv` 幂等导入逻辑（按内容哈希去重、损坏文件进 `quarantine/`），而不是新写一套独立导入脚本。

### 7.8 本地换脸生产路径规范 (`swap_single_face_facefusion`)
- 对齐裁剪的插值方式必须按仿射变换的实际缩放比例动态选择（`sqrt(|det(M[:, :2])|)` ≤1 用 `INTER_AREA`，>1 用 `INTER_CUBIC`），禁止固定使用 `INTER_AREA`——pixel-boost 对中小尺寸人脸是放大而非缩小，`INTER_AREA` 在放大场景会退化为接近 `INTER_NEAREST` 的效果。
- `FaceOccluder.create_occlusion_mask()` 的羽化半径必须随传入裁剪的实际宽度等比缩放（基准：256px 对应 sigma=5），禁止改回固定像素 sigma，否则大分辨率（pixel-boost 放大后的）特写会比小脸有明显更硬的遮挡边缘。
- 色彩校正只允许使用"边界环"模式接入生产路径（`reinhard_color_transfer` 计算全脸调和结果，但只取 mask 与其腐蚀版本之差的窄环混合回原始换脸结果）；**禁止**在生产路径 `swap_single_face_facefusion` 中启用全脸 Reinhard 迁移——全脸迁移改变换脸区域本体色调，存在识别漂移/色彩饱和度整体偏移的风险。旧诊断变体 `swap_single_face_seamless()` 已退役，不得将其作为回退、实验开关或受支持替代路径恢复。
- 合影多人映射的人物-人脸分配必须使用全局最优算法，见 7.3。
- 语义区域保护（`core/region_parser.py` 的 `RegionFaceParser`，`models/bisenet_resnet_34.onnx`）与 XSeg 遮挡 mask、106点解剖学 mask 通过 `np.minimum.reduce` 一起取交集，加入新保护区域时优先在 `create_region_protection_mask(protected_regions=(...))` 里追加 `core/region_parser.py::REGION_CLASSES` 中已核实的类别名，禁止凭记忆猜测新的类别索引——该模型是 19 类 CelebAMask-HQ 语义分割，索引已对照 FaceFusion 官方源码与真实推理输出核实（详见 [docs/facefusion-local-swap.md](docs/facefusion-local-swap.md)），扩展前应类似地用真实图片跑一次推理确认新增类别的行为。

### 7.9 模型资产完整性核查规范 (Model Asset Verification)
- 遇到 `models/` 下大小异常（几字节到几十字节）的 `.onnx` 文件时，先用 `file`/`cat` 检查内容，不要假设它是有效权重——本项目已经发生过失败下载留下纯文本错误页（如 `Entry not found`）伪装成 `.onnx` 扩展名的情况（`bisenet_resnet_34.onnx`/`face_parser.onnx`），`models/README.md` 会记录已知的此类问题。
- 新增/修复模型资产前，必须用 `onnxruntime.InferenceSession(...).get_inputs()/get_outputs()` 核实真实的输入输出张量名与形状，并尽量在一张真实测试图片（`tests/fixtures/`）上跑一次推理核对输出语义（如类别数、类别分布），不要只凭文件名或代码里的猜测就接入生产路径。
- 新增模型下载源必须验证 SHA-256（或来源方提供的完整性摘要）与实际下载文件一致，并在 `models/README.md` 与 `scripts/download_models.py` 中同步记录来源 URL、许可证与摘要。

### 7.10 文档事实同步与冲突治理 (Documentation Governance)
- `docs/documentation-governance.md` 定义文档权威层级和证据矩阵。行为、默认值、模型、状态机、接口、存储或失败语义发生变化时，代码修改必须在同一变更中更新对应权威文档、`README.md` / `DESIGN.md` 摘要和 `CHANGELOG.md`；不得把文档更新留作后续任务。
- AI 精修以 `docs/ai-refinement.md` 为权威设计，本地换脸以 `docs/facefusion-local-swap.md` 为权威设计，模型目录以 `config/ai_gateway.yaml` 为可执行事实源并由 `docs/model-catalog.md` 镜像。`CHANGELOG.md` 只记录历史，不得作为当前能力依据。
- 非显而易见的能力声明必须至少链接到生产代码/配置、回归测试或带输入/指标/校验和的可重复实测记录。不得根据未使用模块、文件名、Prompt 文案、旧变更记录或供应商营销名推断“已支持”。
- 删除或替换架构时必须移除相互冲突的现行描述，或明确标为“已退役历史实现”；禁止在两个文档中保留两套同时生效的状态机、默认值或模型清单。
- 文档修改后必须运行 `uv run pytest tests/test_documentation_consistency.py`。新增可机械验证的不变量时应同步扩展该测试；涉及 Python 文件仍须按第 3、6 节执行完整 Ruff 检查。

### 7.11 批量任务历史与结果回看 (Batch Task History)
- `docs/task-history.md` 是批量任务历史的权威契约。服务端 SQLite 快照是任务状态事实源；浏览器 `localStorage` 只能保存不含图片的轻量工作区关联，不得覆盖服务端状态。
- 新增历史列表接口必须返回有上限的轻量摘要，禁止为展示历史列表传输完整 `results`、请求体、原图/结果 URL、base64 或 data URI；完整详情只能在用户打开单个任务时按 `task_id` 获取。旧 `GET /api/v1/batch-tasks` 完整列表仅为兼容性保留并已 deprecated，新代码不得继续依赖。新增历史类型时继续使用“summary projection + detail aggregate”，不得直接复用重量级详情对象。
- 新成功结果必须在提交结果时同步保存后续复核所需的不可变原始画布和处理产物，并只通过受控 `/api/v1/files/` URL 暴露。任务记录或文件被清理时明确返回不可用，禁止猜测路径、重建替代结果或把缺失结果显示为成功。
- 历史查看器必须为只读边界，不得把旧任务的 source、target、mapping、mask、结果或精修状态静默写入 `useStudioStore`。只有 `QUEUED`/`PROCESSING` 且具有本机明确关联的任务可以重新连接活动工作区。
- 服务端和浏览器信息合并时，服务端状态始终优先；浏览器数据只允许补充目标名称/ID和工作区关联。相关变更必须覆盖摘要不泄漏图片载荷、跨重启恢复、无本机元数据仍可查看、结果文件缺失降级等测试。

### 7.12 工程边界、死代码与 AI 开发防回归 (Engineering Architecture)
- `docs/engineering-architecture.md` 是工程依赖方向、生产/诊断/退役代码分类、兼容策略和模块拆分准则的权威文档。进行跨模块重构或清理前必须先核实生产入口、测试引用、文档契约和运行资产，禁止仅凭文件名或“看起来没用”删除代码。
- `web/src → api → core → provider/runtime` 是单向依赖。`core` 禁止反向导入 `api`；`core/__init__.py` 与 `api/__init__.py` 必须保持无副作用，禁止为了缩短 import 而在包入口重新导出推理模型、创建 FastAPI 应用或设置全局运行状态。修改包边界需跑 `tests/test_package_boundaries.py`。
- 每个实现必须明确归类：生产代码必须从当前 API/CLI/UI 入口可达；诊断/评测代码只能放 `scripts/` 并标注非生产；退役实现应从当前源码、导出、启动检查、下载器和仅为其存在的测试中一并移除，历史只留在已明确标记的 Changelog/Git 历史中。禁止保留与权威架构冲突的“影子流水线”作为未经批准的回退。
- 兼容分支只有在具备明确调用方、回归测试、弃用说明和删除条件时才能保留。不得用无边界的 `hasattr`、双配置格式或宽泛异常吞噬来永久兼容未知旧实现；兼容期结束后应删除分支和对应迁移代码。
- 跨层事实必须只有一个可执行来源：模型/角色/生成能力来自 `config/ai_gateway.yaml`，模型下载摘要来自 `scripts/download_models.py`，API 契约来自 `api/schemas.py`，任务状态来自服务端持久化。前端、评测脚本和文档不得另建别名、默认值或能力清单；可读文档中的重复事实要由一致性测试约束。
- 模块按“变化原因”拆分，不按行数机械拆分：展示、纯计算、外部 I/O、任务状态机、持久化和 provider 协议应形成可测试边界；同一状态机的连续步骤不应被拆成多层一行转发。新抽象必须至少消除一份重复事实、隔离一种副作用或形成可独立测试的稳定边界，否则遵循 KISS 保留直观函数。
- 未经实测的数据不得伪装成精确默认值或用户提示。批任务耗时等运行指标优先从已完成任务的真实数据推导；样本不足时明确表达“暂无可靠估计”，禁止继续写死貌似精确但无证据的常量。
