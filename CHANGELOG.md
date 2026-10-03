# Changelog

## 2026-10-03

- 新增非生产评估适配器 `scripts/swap_eval_adapters.py`（HyperSwap 1a/1b、AlphaFace、Ghost 2/3）及 FaceFusion 模型许可核对；8 个模型身份相似度对比见 `docs/facefusion-local-swap.md`。生产默认与路由不变，权重仅本地评估。
- 身份相似度脚本的 `--strengths` 改为 `--arms MODEL[:强度]`，可对比换脸模型并记录耗时；回退到别的模型的结果不计入该臂。新增 InSwapper/HyperSwap/Ghost 身份相似度对比记录，不改默认模型或路由。见 `docs/facefusion-local-swap.md`。
- 身份相似度脚本支持 `--strengths` 扫描并增加像素差/检测分代理指标；记录 6 张被跳过脸的原因和 0.72–0.95 强度扫描（默认 0.9 不变）。见 `docs/facefusion-local-swap.md`。
- 新增非生产脚本 `scripts/evaluate_identity_similarity.py` 并记录真实照片 × 素材库的身份相似度实测（105 次，部分迁移、非完全身份迁移）；见 `docs/facefusion-local-swap.md`。
- 新增非生产脚本 `scripts/smoke_portable_real_ai.py`（需 `--allow-paid`），并记录一次授权的真实 AI 端到端实测；见 `docs/windows-portable-validation.md`。
- 工程素材接口在主机可用内存不足时返回带说明的 HTTP 503，替代未处理的 500；Windows 便携包重建为 2026-10-03 完整版，合入 9/30 的磁盘历史、性能排队与低内存补丁。

## 2026-09-30 — 共享内存队列、分阶段证据和局部回贴

- 精修轮次/冻结图片与本地推理串行；缓存缩略图不等待模型，取消读取先等线程结束。
- Windows 便携启动器限制 HTTP 关闭等待，避免浏览器 SSE 长连接阻止正常退出。
- 分析和复评逐张解码证据，生成保持单张原分辨率输入，候选落盘后释放数组。
- 版本与参考图使用 384px JPEG 磁盘缓存，旧记录自动补建且不改变原图、接受指针。
- 人脸回贴仅分配投影区域；合成 1200 万像素例子的步骤跟踪峰值约下降 72%，
  最大通道差异 1。不是整程序内存承诺；详见内存调度文档和对应验证报告。

## 2026-09-30

- 释放源人物大图常驻数组，授权批量精修的实际参考证据无损落盘；工程成功保存后用磁盘引用替代内联照片，恢复逐张验证。修复精修图片读取重复解码导致低内存时显示失败，保留摘要校验并新增图片重载；接受后等待关联工程保存，历史按服务端分页访问较早会话。

- 修复批任务读取请求或初始化异常时永久保留排队状态；记录失败并释放队列。新增实际模型后端与本地执行槽状态，区分内存等待、参考素材分析、原生推理及取消后的收尾；新增只读 `Check-Performance.cmd`。关闭串行模型闲置线程池的空转，CPU 对照测试输出像素一致，不改分辨率或保护掩码。

- 修复大图创建精修会话失败：合法 JPEG 转换为无损 PNG 后不再被外部 20 MiB 限制误拒绝；同步修复快照读取、发布、生图输入和工程保存恢复，保留像素、内存及摘要校验。已知输入错误返回具体原因，未知异常继续脱敏。

- 修复单图检测返回 `global_person_id: null` 时，多个空编号被合并为一个人物并生成非法自动映射的问题。未知人物或未完成分析时不自动配对；执行前拒绝无效人物编号，映射弹窗排除旧工程中的损坏绑定，允许重新明确选择目标脸。

- 修复批任务历史请求长期占用内存：等待任务仅持有 ID，执行时按需读取不可变 SQLite 请求，终态释放；旧失败记录内联原图按详情读取，保留无请求快照的工程恢复能力。旧精修档案不再预加载不用的请求图片。
- 修复精修删除后的失效最终图、下载遇到网络错误静默使用旧副本、任务丢失后图片仍锁定，以及切换工程残留上一任务状态；成功保存后释放工程素材去重缓存中的旧图片。
- 加固便携构建依赖复用与目录发布：固定输入指纹和完整 runtime 摘要核验，全流程暂存、可捕获发布失败回滚。独立 ZIP 与 GPU 差分包同时校验和发布摘要，失败回滚；拒绝发行目录符号链接。损坏安装的自检保留已有发现，缺少 dotenv 也能输出基础诊断。

## 2026-09-29

- 加固 Windows 完整包构建：强制构建并替换前端，默认携带自检入口，提前拒绝私人运行目录，按规范摘要修复已知 tokenizer 缓存换行问题，ZIP 全文件校验通过后原子发布。启动前检查数据目录可写性，补充模型加载提示；本次个人 CPU/GPU 包采用短根目录并按授权携带配置和素材。

- 修复带 MPO/MPF 信息的相机 JPG 在推理前被拒绝、导致人脸检测 400 和工程图片保存 422：按 JPEG 主图读取，工程原始字节不重编码，回看统一为 JPEG MIME；保留图片大小、像素、内存与其他格式限制。回归覆盖多帧主图选择、限制及工程保存/恢复。

本文件记录 PersonaWeave 的版本演进与功能变更历史。历史条目中的 DMX 专属配置、适配器、模型别名与分辨率/定价描述均为已退役历史；旧逐脸 scope 表单、单任务精修状态机和相关文件路径也仅为历史记录，不代表现行生产入口。当前模型契约见 `docs/model-catalog.md`，精修会话契约见 `docs/ai-refinement.md`。

---

## 2026-09-28

- 修复前端样式体系的产出级缺陷（`web/tailwind.config.ts`、`web/src/index.css`）。补齐 `slide-in-from-top-2` 关键帧：调用处已写该类名，但项目未启用 `tailwindcss-animate`，一直零产物，滑入动效从未生效。`boxShadow` 的 `primary`/`cyan` 键改名为 `glow-primary`/`glow-accent`——键名与 `colors` 同名时颜色插件会额外输出一条 `.shadow-<key>` 并重写 `--tw-shadow`，劫持该 token 的几何与透明度（历史表现为主按钮发光渲染成 100% 不透明纯色，而非配置的 0.35 alpha；同批的 `shadow-cyan` 因颜色键是对象、无 `DEFAULT` 而未受影响）。

- 精修工作区触摸目标统一为 `min-h-touch` / `min-h-touch-sm`（44px / 40px）工具类，删除 `.refinement-workbench` 的 40px 硬补丁。该补丁特异性 (0,1,1) 高于工具类 (0,1,0)，会把调用处已写对的 44px 反向压回 40px；现改用 `:where()` 将兜底特异性归零，且不再对 `a.btn` 生效（Tailwind 会把 `.btn` 的 `@apply` 链在复合选择器下逐变体展开，产物由 12 条规则降至 2 条）。`.btn-accent` 删除：白字在 `#06b6d4` 上仅 2.43:1（AA 双阈值失败），且全项目零使用点。

- 收敛默认调色板裸用与对比度：新增 `primary-soft` / `err-soft` 两个文字 token（`#5b5fef` 在 `primary/15` 底上仅 2.95:1，`#ef4444` 在 err 系浅底上 3.89–4.45，均不达 AA），替换 `.seg-item-primary`、`.chip-primary`、`.icon-badge-primary`、`.val-badge-primary`、`.chip-err`；`text-zinc-500` 62 处升至 `text-zinc-400`（其在项目各底色上 3.15–3.90）；画布底色统一为 `bg-canvas`；Logo 渐变、人脸标记与对比分割线改用 `primary`/`accent` token；`MultiPersonMatrixModal` 的 tween 过渡改为项目 spring 参数。

## 2026-09-23

- 按用户审美更新个人素材包：虚构中国年轻人、自然上镜风格，男女各 32 位，每人 16 张角度与表情参考；新人物使用独立编号，旧素材归档保留历史关联。新安装完整包仅携带当前可选素材，避免归档图累积增大包体积。制作与验证见 `docs/portrait-chinese-validation.md`。

- 优化多图内存峰值：顺序读取上传、聚类原图临时无损落盘、批任务串行及逐图释放、请求快照首次持久化；修复取消等待者过早释放推理槽位。Windows 同时检查可用 RAM 与提交额度，内存预算不足等待恢复，超时明确停止并保留完成项。融合改为逐行分块，12MP 跟踪分配峰值减少约 80%，像素校验一致；批量 AI 精修等待本图轮次结束后继续，关联先持久化且不重复付费提交。见 `docs/memory-scheduling.md`。

- 补齐换脸失败弹窗：单图、批任务提交/终态、人物分析与映射、精修失败及候选复评失败显示具体原因，需要手动关闭；保留 FastAPI `detail`，批量失败按图片汇总并防止轮询重复通知。
- 素材库支持显式年龄归档，隐藏不再使用的人物但保留历史 ID 与图片；增加生成图板的自动裁切、人脸检测、左右侧脸覆盖及身份一致性验证工具。

- 核实并修复 CPU/GPU 便携包漏带 ONNX Runtime 直接依赖 `msvcp140_1.dll`；VC 清单扩展为五个组件并在构建前检查，独立运行库修复在 PowerShell 中备份/更新应用 DLL，保留已有源码补丁及用户数据，不依赖开发环境。

- 增加 Windows CPU/GPU 原始交付版的小型源码修复包与本机自检入口，复用已有版本、摘要和运行锁校验，保留配置、素材和工程；明确旧包与单独修补的测试目录不同，不能仅凭 422 认定缺少运行库。

## 2026-09-14

- 修复导入素材库人物后工程保存返回 422：工程归档支持受控缩略图/原图地址，按 SQLite 素材 ID 校验并独立保存预览字节；参考原图用途保持不变。覆盖中文目录、保存/恢复、删除原素材后回看和目录越界拒绝。

- 修复本地 LiteLLM Router 初始化错误被误报为上游 HTTP 400：区分本地配置与请求失败，保留异常脱敏与零自动重试；便携版配置提示明确 OpenAI 兼容模型填写 `openai/实际模型名`。

- 修复 Windows 中文目录下素材与参考图读取失败、工程受控图片保存返回 422，以及批任务/CLI 结果写入失败：统一经 Python 文件访问和 OpenCV 内存编解码，保留受控路径限制与失败语义。增加中文路径、图片无损往返、工程素材导入及生产入口防回归检查；已交付便携版须补丁后重启。

## 2026-09-11

- 新增 Windows NVIDIA GPU 完整便携包与 CPU→GPU 差分升级包，避免重复下载模型/界面；保留配置与用户数据，校验版本、文件摘要及运行锁，支持中断后重试。Windows 驱动探测与随包 CUDA DLL 加载独立处理，六个生产模型以真实 CUDA profiling 验证，见 `docs/windows-portable-validation.md`。
- 新增 Windows x64 CPU 便携构建与双击启动入口：内置独立 Python、固定 Windows 依赖、六个校验后的生产模型和前端；不携带开发者配置或历史。支持单实例保护、端口自动避让和首次 AI 配置，说明见 `docs/windows-portable.md`。
- 修复接受精修后准备区预览/对比仍显示本地初稿，以及 ZIP 仍导出旧结果的问题：按明确会话关联同步已接受版本，刷新恢复可补同步；当前工程批量导出优先使用服务端接受版，保留原始图片字节和真实格式，已接受文件失败列入清单而不回退成本地初稿。
- 补修旧工程遗漏的已接受会话关联：只在原图/本地初稿、稳定人物与参考摘要完全匹配且候选会话唯一时关联，不按名字猜测、不改变接受状态；已有对应和歧义记录不重绑。

- 全面复核工程恢复：统一工程列表、任务/会话历史和刷新时的缺失输入修复；运行中批任务持久化待完成的精修参考，重连仅接回同一任务；补齐无会话图片切换的自动保存。覆盖单源未指定脸序、重复/缺失对应、已删除任务、已替换画布与编码不同但像素一致等回归，保留缺失旧数据的明确限制。

- 修复旧批任务恢复后虽然有本地结果却无法创建精修：从保存的逐图人物匹配和明确参考素材补齐精修输入，校验原图/结果摘要；再次恢复也可修复已迁移工程，不覆盖现有编辑。同批但未匹配到当前图的跳过角色不再阻止已确认人物创建精修，缺失对应仍不猜测。

- 新增完整编辑工程自动保存与显式恢复：独立保存原图/结果/素材、稳定人物映射、参数、逐图保护画笔和撤销/重做，以及精修基图/查看版/确认/意见草稿。历史页新增工程列表、删除和两类任务的恢复入口；图片校验、版本冲突及原子恢复保护当前编辑，旧记录按真实保留内容迁移并列出缺失项，不自动换脸或生图。

- 按测试反馈将网关默认等待从 180 秒延长至 300 秒，精修分析和复评外层同步调整为 330 秒；保留图像编辑的独立超时和零自动重试，避免视觉请求尚未等满就被外层取消。

- 完成用户授权的五人合影两轮真实付费精修及逐脸审阅：人工意见和 V0→V1→V2 链校验通过；两版均出现相貌漂移和遮挡细节错误，未定稿。第二轮复评 HTTP 408 后候选完整保留，详见 [实测记录](docs/refinement-live-test-20260911.md)。

- 精修人物参考栏将详细空间定位默认折叠，保留人物编号和缩略图，避免多人时定位文字挤满左栏。

- 精修画布默认完整展示选中版本，原图/初稿分屏改为显式选择，避免右侧目标人物被默认原图半屏遮住而误以为结果没有变化。

- 真实复评在 1600px 整图预览下未能核对小脸：视觉预览上限提高至 3200px，参考板仍使用有界排版；原尺寸试请求收到上游 HTTP 500，未恢复无上限传图；界面显示模型原生输出/画布尺寸及放大提示。模型内部采样和细节质量仍需实测，不宣称像素尺寸等于真实画质。

- 提示词版本 conversation-refinement-v5：真实分析曾把“查阅参考板”和“本轮不生成”写进编辑计划；现在要求把参考观察转写为可独立执行的视觉修改，将讨论状态说明留在回复中，保持生图单张当前图输入。

- 提示词版本 conversation-refinement-v4：将缺参考的不确定性说明、人物对应和编辑计划一致性规则补充进首请求 JSON schema 的字段描述，保留严格校验与零自动修复。

- 真实测试暴露精修失败原因过于笼统：增加上游 HTTP 状态、超时及格式校验类型的安全分类，不保存上游错误正文、模型返回值或凭据，也不增加自动重试。

- 真实视觉调用发现交互会话的纯编号定位无法核对人物：改为冻结原图检测的空间顺序和可用脸框中心百分比，稳定人物 ID 不与脸序混用；旧会话通过讨论澄清，不改写已冻结证据。

- 精修增加明确的分析确认步骤，意见输入与生成只在确认后启用；换基图清理旧确认和意见，新分析也需重新确认。
- 问题按人物/五官、遮挡/融合、光影/质感、构图分组折叠，生成复评独立展示，减少重复文本和计划堆叠。
- 生成请求增加 diagnosis_turn_id，服务端验证归属、基图与讨论状态并绑定精确分析，幂等重试不会改用新分析。
- 提示词更新为 conversation-refinement-v3，要求简短结论与具体证据，强调多人、自然侧脸透视和跨面部小物体的遮挡关系；不承诺文字能恢复严重身份偏差。

## [Unreleased] - 2026-09-07

### 工作台视觉与交互复核

- 修复窄屏工具文字竖排、结果下载溢出和悬浮操作遮挡图片；工具按视图/定位分组，当前图精修与下载各保留一个固定入口。手机导航保留页面名称，准备状态标签降低视觉干扰，预设卡片和通用小按钮统一尺寸。
- “替换目标”改为“定位画面人物”，明确实际换脸对象来自角色映射；快捷键使用 Radix 对话框，隐藏工作区或打开对话框时不响应画布快捷键，窗口失焦恢复原对比状态。处理期间禁止修改右栏参数和移除目标图，单图等待不伪造百分比。
- 历史列表区分加载、失败和空记录，中文状态与日期统一，选择框支持半选；精修空页面增加返回准备/选择已有图片入口，尊重系统减少动态效果偏好。
- 新增不调用真实任务/模型的 `scripts/smoke_studio_ui.py`，截图覆盖准备、结果、历史和精修及 320/390/1024/1440 像素宽度；验证可见操作不越界并保留纯状态底栏。

### 换脸操作入口归并

- 右侧“换脸控制”集中角色映射、单图/批量启动、AI 首轮授权、重连与 ZIP 导出；窄屏通过同一侧栏抽屉访问。顶部准备项改为状态标签，底部只显示任务状态、进度与实际耗时。
- 批任务提交与轮询移至 App 唯一挂载的 `useBatchPipeline`，删除自定义事件桥接；关闭侧栏或切换工作区仍接收结果，AI 授权不会随抽屉关闭丢失。单张目标显式授权 AI 时仍可执行完整任务。
- 补充入口唯一性、纯状态栏、抽屉关闭后任务继续以及单图 AI 授权回归。

### 历史记录删除补齐

- 精修会话与旧版档案增加 revision 校验的删除接口，清理各自版本、定稿和证据；运行中记录拒绝删除，文件移动或数据库提交失败保留记录并恢复已暂存图片。
- 历史页增加精修/旧版的单条和批量删除；精修工作台可直接删除当前会话。成功后清理列表、详情缓存及对应轻量关联，失败记录保留并刷新，SSE 通知已打开的会话关闭。
- 本地批任务已有删除继续保留，并补齐详情缓存和失效选择清理；新增删除隔离、数据库失败恢复、重启、版本冲突及界面回归。

### AI 精修流程与界面整理

- 交互入口改为“检查问题 → 意见与生成 → 对比与定稿”，首次检查只分析；当前基图分析与人工意见集中展示，旧轮次折叠，已关联图片不重复列出，接受与导出集中到画布。
- 生成前完整传递同一基图最近已完成的诊断，结合人工意见整理计划；当前修改意图优先于冲突的旧建议。切换基图不借用其他版本的问题，仍保持单图单次生成、人工接受与原有批任务 auto 授权边界。
- 更新请求组合、分支与界面交互回归及浏览器诊断脚本；提示词版本为 conversation-refinement-v2。

### 审查修复：契约、冗余与测试

- 精修创建在快照写入前累计原图、基线和参考图的解码像素，超限拒绝并清理本次未注册目录；执行轮次复用相同预算，包括无参考图场景。回归覆盖边界、重复参考、跨人物累计、幂等重试与已有会话保护。
- VLM 仅建议跨图聚类合并，合并前按请求阈值复核实际参考与候选的 ArcFace 余弦；缺失或无效特征不合并。106 点提取异常时仍应用默认椭圆保护，并保留失败与降级诊断。
- 删除无调用的按下标目标更新接口、收窄仅内部使用的导出；合并重复路由/环境变量测试，数学回归改为确定性数值断言，包边界检查对准当前精修模块。保留 legacy 档案、诊断 CLI 与加载语义不同的参考提取入口。
- 修正文档中 AI 配置无效会导致整体启动失败、所有模型操作统一返回 503、旧质量评价模块归属等过时描述；新增无效生成策略仍可启动本地服务的回归。

### 对话式整图精修工作台

- 精修升级为 SQLite 持久化的 session/turn/version：冻结原图、本地初稿和按稳定人物 ID 绑定的实际参考；查看版本、下轮基图和已接受版相互独立，支持显式从旧版本分支。
- 多模态诊断与复评采用三类证据；允许无问题、身份不可判断和定位待澄清，不再强制找刺或使用分数门槛。生成只接收当前完整图与文字，允许需求内整图重绘，不携带原图/参考板/mask，也不做局部回填。
- 后端区分 discuss/generate/auto，每轮最多生成一个候选；复评失败保留候选并记录提示，只有人工接受才发布。会话幂等请求、revision/digest 校验、SSE 快照恢复与重启策略均不自动补发付费调用。
- 新接口归 api/refinement 与 refinement_sessions 路由；退役旧 core service/plan、单任务生成入口和旧精修表单。legacy 档案只保留查看、质量评价与人工接受，旧创建/诊断/计划/取消/重试返回 410。
- 新提示词首请求包含完整 JSON schema，精修显式 max_text_repairs=0；通用网关默认两次文本修复仍保留给仲裁调用。记录 Anthropic/OpenAI 官方提示方法与取证限制，新增离线契约/像素/状态测试，不宣称真实付费画质已验证。

### LiteLLM 去供应商化重构

- 原生 `model_list` 统一视觉与图像 deployment，稳定别名支持多 deployment；`image_models` 仅保留引用同目录的展示与 generation metadata。默认目录收敛到两个通用别名，不再内置供应商模型或伪造免费定价。
- 视觉/图像的模型、URL、Key 分别由 `AI_VISION_*` / `AI_IMAGE_*` 配置；视觉走 `Router.acompletion`，图像走 `Router.aimage_edit`，退役手写 DMX 协议与私有回退。默认尺寸为 `size: auto`，不再承诺 4K；离散尺寸和动态范围必须显式声明。
- 默认 Router 重试与图像 deployment 重试均为 0；可显式配置同能力 fallback，图像须相同 generation，增加重试可能重复付费。目录 API 保持形状且只返回非敏感信息，前端无需改动。
- 旧 DMX 变量、provider/params 配置和退役别名不兼容，不做静默映射；用户按 `docs/model-catalog.md` 自行填写通用配置。已有候选的人工接受仍按 revision/digest 发布原 staging 字节，不重新生图、不迁改运行数据。
- 同步架构、精修、工程规则和迁移文档，保留其他文档一致性断言并增加原生目录与禁旧路径不变量。

### 2026-09-08 文档精简

- README 收敛为功能、启动与文档入口；DESIGN 改为架构概述，移除旧路线图、退役算法展开和重复接口/模型说明。
- 精简工程规范的重复说明；两份独立选材/强度文档合入 `docs/swap-benchmarks.md` 后删除旧文件，保留实测原始摘要、复现路径和适用限制。
- 更新文档导航与历史证据引用，增加本地章节链接检查；现行状态机只在精修权威文档维护，概述改为引用。

### 2026-09-08 死代码与失效测试清理

- 经 API/CLI/UI、脚本、测试和现行文档交叉核查，删除三个换脸适配器未被生产使用的 `get()` 回贴、`swap_face()` 和私有对齐分支，以及只被旧路径和测试使用的 `utils/affine.py`。生产继续通过 `infer_aligned()` 推理，保护遮罩与回贴统一由流水线执行。
- 同步删除两个旧仿射工具测试和一个旧 HyperSwap `get()` 回贴测试；保留当前流水线遮罩、像素保护、模型路由及兼容数据恢复的回归测试。流水线直接使用已有标准化模板，消除生产与测试之间的重复常量。
- 删除无调用方的 `create_identity_gallery()`、`filter_target_faces()` 与空的 `make_context_sheet()`，以及前端 `cn`、未用图片地址函数；批任务请求复用已有 `BatchImageItem` 类型。
- 删除无引用的 `clsx`、`tailwind-merge` 和 Radix progress/tabs/tooltip 直接依赖，使用 pnpm 离线更新锁文件；保留诊断脚本、模型资产和已有持久化数据。
- 修复两个仍有诊断用途的精修脚本使用旧手工请求对象、缺少编辑范围导致生成前失败的问题：复用 API 请求契约，端到端脚本加载 `.env`；新增离线回归覆盖单图生成和复评失败时证据保留。

### 按领域整理工程结构

- `api/app.py` 收敛为应用装配入口；生命周期、依赖访问和功能路由分开。任务与持久化归 `api/tasks/`，素材库归 `api/portraits/`，图片 I/O 从批任务抽离为 `api/media.py`。
- AI 配置、网关、契约和 provider 统一归 `core/ai/`；配置解析不加载 LiteLLM，通用结果类型不依赖具体 provider。精修计划与服务归 `core/refinement/`，输入快照 I/O 与任务状态转换分离。
- 前端精修和历史组件、规则及测试归 `web/src/features/`；精修草稿/诊断/轮询集中到 `useRefinementController`，弹窗保留展示和本地缩放。跨目录使用 `@/` 引用。
- 新增固定版本 Prettier、前端 `format` / `format:check` 命令，`lint` 结合格式检查与 TypeScript；统一展开压缩代码。扩展包边界回归，同步权威文档和目录说明。
- 修复 uv editable 安装默认指向不存在的 `src/`：显式声明顶层源码包，CLI 实现归 `api/cli.py`，根目录 `cli.py` 保留为源码可执行入口；验证 wheel 构建和两个 CLI 的 `--help`。
- 启动入口 `api.app:app`、CLI 命令、HTTP 契约、模型配置和运行数据目录保持不变；仓内旧 import 路径已迁移，不保留转发模块。

### AI API 接入与精修生命周期审查修复

- 修复复评响应校验失败或复评期间重启导致已生成候选被判失败的问题；有效候选保留并携带提示性 `review_error`，关停不再等同人工取消。
- 精修输入、人物集合和模型选择按轮次持久化；继续精修失败及跨重启重试不再退回最初上传图，缺失/损坏输入明确失败。无输入记录的旧多轮任务不猜测恢复基线。
- 接受操作发布已校验的同一字节缓冲区，临时文件改放私有 staging，常规发布异常回滚待接受状态；恢复接受后清除候选 URL 和 staging。
- 修复缺少密钥时 AI 健康状态误报就绪，以及 `DMXAPI_API_BASE` 被 YAML 固定地址绕过；Gemini 在所配置来源使用原生协议路径。DMX 模型 POST 拒绝重定向，防止图片及自定义密钥头跨域转发。
- 回归覆盖见 `tests/test_refinement_tasks.py`、`tests/test_ai_gateway_config.py`、`tests/test_ai_readiness.py`、`tests/test_dmx_gemini.py`；权威契约同步到 `docs/ai-refinement.md` 与 `docs/model-catalog.md`。

### 一次成片质量闭环与定向精修

- 新增逐人显式修复范围和身份自评提示；预览、生成和建议性复评共用 `core/refinement/plan.py`。默认不开放头发、耳颈、视线、表情和头部姿态，身份不足提示返回本地，不新增强制门禁。
- 新增候选digest绑定的人工成品评价、不可变三图证据及服务端生成尝试/来源关联；评价不触发接受，未评价/未知来源/生成失败单独统计，不能冒充一次成片率。
- 新增非生产最佳单张、图库、80/20三策略受控对照与素材体检；不改变生产默认值、身份条件或遮罩保护。完整契约见 `docs/delivery-quality.md` 和 [统一评测说明](docs/swap-benchmarks.md#source-strategy-comparison)。

### 可信评测与逐脸诊断

- 修复消融映射缺目标特征导致原图被当成对照结果；评测改用几何一对一关联、完整预期样本分母与明确目标身份，拒绝无标签/缺失样本伪高分。见 `docs/swap-benchmarks.md`。
- 明确 best-match 为 80% gallery + 20% best；新增安全逐人 matching/source_selection/mask 元数据，区分匹配冲突与低阈值，记录保护组件缺失/降级。API/CLI显式传真实聚合权重，未知来源不猜。
- BiSeNet眼镜保护羽化按裁剪宽度缩放（256px sigma5），不取消遮挡或眼镜保护；新增尺度与降级回归。
- 全部终态开放只读历史详情，保留失败图片和已有成功产物；重启后按已持久化结果恢复计数。诊断拼图改为实际gallery来源板与真实best索引，旧“Selected source”单张展示不再作为输入事实。

### 换脸可见强度与失败诊断

- 基于四张真实图片的 72% / 90% / 95% 受保护对照，将 InSwapper 的 API/流水线/Studio 默认强度调至 90%，工作台上限从 90% 对齐至 API 的 95%；不放宽人物匹配、可见变化阈值、侧脸或遮挡保护。证据和适用边界见 [强度实测记录](docs/swap-benchmarks.md#recorded-strength-comparison)。
- 新增非生产只读复现工具 `scripts/diagnose_batch_strength.py`，保存逐脸模型输出、遮罩、融合和素材相似度证据，不覆盖任务历史或发布诊断候选。
- 修复批任务零换脸时异常分支丢失真实检测数、人物结果和模型路由的问题；零换脸仍为 FAILED 且不发布图片，旧历史缺失字段不自动重建。

### 🎨 换脸质量：遮罩限定的修复重接入 + 可调色彩迁移

- 新增 `core/restorer.py` (`CodeFormerRestorer`)：换脸模型的有效工作分辨率固定封顶在 256×256（见 `swap_single_face_facefusion` 里的 pixel-boost 说明），在高像素照片上贴回后明显比周围原图更糊。CodeFormer 提供 `fidelity_weight` 身份保真度旋钮，因此被选中重新接入；GFPGAN 没有等价的身份控制，**仍保持退役状态**，未被接回。
- 这是对 0.1.0 时期"全脸修复"能力的**限定范围重新引入**，不是简单复原：修复网络输出只通过 `crop_mask`（换脸本身已在用的解剖学/遮挡/区域保护遮罩）混合回画面，绝不会越过该遮罩去动背景、被遮挡区域或受保护像素；默认 `enable_face_restoration=false`，需显式 opt-in；`restoration_fidelity_weight`（默认 0.75，偏向保留已换上的身份而非生成式幻想细节）与 `restoration_strength`（默认 0.5，原始换脸输出与修复输出的混合比例）均可调。
- `api/schemas.py` 的 `SwapParameters` 新增 `enable_face_restoration`/`restoration_fidelity_weight`/`restoration_strength` 三个参数，贯穿 `api/tasks/batch.py` 全部 4 个换脸调用点。
- 边界环颜色迁移的强度与宽度（此前硬编码 `blend_ratio=0.45`、环宽 15px 基准）改为可配置参数 `color_transfer_ring_strength`/`color_transfer_ring_width_px`，默认值保持不变，等于 8-25 那次"更宽/更浓的环导致过暗过掉饱和度"回归后收敛出的安全值——调宽/调浓仍是调用方显式承担的已知代价，不做场景自适应默认放宽。
- `tests/test_download_models.py` 的退役资产守护测试拆分：`test_gfpgan_face_restorer_is_not_a_runtime_asset` 继续断言 GFPGAN 不可达；新增 `test_codeformer_restorer_is_optional_not_startup_required` 断言 CodeFormer 是可达但非启动强制的可选生产资产，不再笼统地把两者都当"退役"处理。`models/README.md` 同步记录 CodeFormer 的本地评测 SHA-256（尚未接入默认下载器，接入时请对实际下载来源重新校验摘要，不要复用此处记录的本地值）。

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
- 新增终态批任务删除：历史面板确认后调用 `DELETE /api/v1/batch-tasks/{task_id}`，同步清理 SQLite 快照、内存状态、本机轻量关联和任务专属图片；运行中任务返回 409，避免工作线程重建已删状态。
- 修复精修接受后仍请求已清理 staging candidate 的 404：接受成功即清除 `candidate_url`，前端接受态改为展示正式 `accepted_url`，旧持久化工作区也不再复用候选地址。

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

- `core/ai/contracts.py` 的 `ReviewIssue.category` 从自由字符串改为固定七分类枚举（`anatomy_geometry`/`bilateral_symmetry_gaze`/`skin_texture`/`boundary_blending`/`lighting_color`/`pose_perspective_scale`/`synthesis_artifact`），`region`/`description` 增加最短长度约束，拒绝"脸部"/"有点怪"这类空泛回答。
- `RefinementService.review()` 的系统 Prompt 新增"不合格 vs 合格"瑕疵描述 few-shot 范例，强制 `region`/`description`/`suggestion` 用中文输出，并要求 `description` 同时给出外观特征、相对范围估计与对比基准三要素。`recommended_prompt` 要求复用 `issues` 中出现过的精确部位命名，避免整体描述又退回笼统措辞。
- `RefinementService.review_local_candidate()` 的 `issues`（字符串数组）同步收紧为中文 + 具体解剖学定位，不再接受孤立判断词。
- 前端无需改动：`RefinementModal.tsx` 已经按 `{region}：{description}；{suggestion}` 拼接展示，具体性提升会直接体现在现有 UI 上。

### 🆕 BiSeNet 语义区域保护（眼镜）

- **新增 `core/region_parser.py`**（`RegionFaceParser`）：加载 `models/bisenet_resnet_34.onnx`，对齐裁剪人脸做 19 类 CelebAMask-HQ 语义分割，生产路径默认保护 `glasses`（class 6）区域——XSeg 只识别"异物遮挡"（手/道具），并不知道某块像素在语义上是眼镜，换脸时镜框/镜片经常被融化变形而毫无保护。已接入 `swap_single_face_facefusion` 的 mask 交集链（box ∩ 106点解剖学 ∩ XSeg ∩ BiSeNet），模型缺失时优雅降级为 `None`，与 XSeg/106点解析器同一容错模式。
- **模型来源修正**：`models/bisenet_resnet_34.onnx` 与 `models/face_parser.onnx` 此前是两个 15 字节的 `Entry not found` 文本残留（失败下载留下的错误页，并非有效 ONNX 资产），`scripts/download_models.py` 也从未收录它们；`models/README.md` 曾记录过这一事实但文件本身未清理。经核实，`bisenet_resnet_34.onnx` 是 FaceFusion 官方模型（vendor yakhyo，MIT 协议，来自 `facefusion/models-3.0.0`），文件名与占位符完全一致，已重新下载（SHA-256 `4a0b8c958a3c938913bd06a8365dbb3c8761afba6ecbf0d14b3b1f77eb230c96`）并纳入 `scripts/download_models.py` 默认下载列表；`face_parser.onnx` 在 FaceFusion 模型仓库中找不到任何对应资产，已删除。
- I/O 契约（输入张量 `input`，512×512，ImageNet 归一化 NCHW；输出 `(19, 512, 512)` 逐类别 logits）已通过官方源码核对，并在真实测试图片上跑通推理验证。

### 🗃️ 旧架构快照：AI Gateway、局部精修任务与人像素材库

- **AI Gateway** (`core/ai/gateway.py` + `config/ai_gateway.yaml`)
  - 配置驱动的 LiteLLM Router 封装，统一管理 VLM 与图像编辑模型别名（`personaweave-vision-review/precision/fallback`、`personaweave-refine-seedream/plus/max/fallback`），支持 fallback 链与 `os.environ/NAME` 语法解析密钥。
  - 所有别名通过 **DMXAPI**（`https://www.dmxapi.cn/v1`，OpenAI 兼容协议的第三方模型聚合网关）调用；`core/ai/providers/dmx_seedream.py`、`core/ai/providers/dmx_responses.py` 是针对 DMX 私有响应协议的自研 httpx 适配器。
  - `core/ai/arbiter.py` 迁移为经由 `AIGateway` 调用，不再直接使用 LiteLLM。
  - `core/ai/contracts.py` 新增结构化输出契约：`FaceReviewReport`、`BatchFaceReview`、`CandidateQualityReview`、`GlobalProtectionReview`、`IdentityArbitration`。

- **局部 / 整图 AI 精修** (`core/localized_refinement.py` + `core/refinement/service.py` + `api/tasks/refinement.py`)
  - 新增 `/api/v1/refinement/review`、`/api/v1/refinement/tasks`（创建/查询/取消/接受/单人重试）、`/api/v1/refinement/tasks/{task_id}/candidate` 接口。
  - 异步任务对整图做一次性 AI 生成编辑，再逐人裁剪局部区域做质量复评；状态机 `QUEUED → LOCALIZING → REFINING → REVIEWING → REVIEW_REQUIRED → ACCEPTING → ACCEPTED`（另有 `CANCEL_REQUESTED`/`CANCELLED`/`FAILED`）。
  - 质量闸门（一致性/自然度/回归/目标问题改善/新增高危问题）+ 整图保护复评（保护物/其他人物/文字/构图四项 AND）全部通过才允许接受。
  - 接受接口带乐观锁（`expected_revision` + `candidate_digest`），不匹配返回 409。
  - 两阶段落盘：候选先落 `outputs/refinement_staging/`，接受后才写入 `outputs/api_results/refinement/accepted/`，未接受的候选不会进入正式产物目录。
  - 前端新增 `RefinementModal.tsx` 承载上述交互。

- **人像素材库 Portrait Library** (`api/portraits/library.py` + `api/portraits/importer.py`)
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

- **[关键] `RefinementService.apply_candidate()` 缺失导致像素保护形同虚设**：`LocalizedRefinementEngine` 早已实现了带硬性断言（越界像素即抛异常）的 `align_generated()`/`composite()`/`apply_manual_protection()`，但 `api/tasks/refinement.py` 从未调用它们——AI 提供方返回的整图候选被直接当作最终产物保存，背景、其他人物、整体色彩与饱和度完全没有代码层面的保护，只能靠 Prompt 请求模型"不要动"和事后的 VLM 抽查。现已新增 `RefinementService.apply_candidate()`，将整图候选通过每人的解剖学 mask 重新配准并合成回原图，只有 mask 支持区域会变化，其余像素逐字节保留。手动保护画笔（`manual_mask`）此前在精修流程中被静默忽略，现同样通过 `apply_manual_protection()` 生效。
- **VLM 诊断分辨率不足**：`RefinementService.review()` 此前只发送一张压缩到 1600px 的完整图片，合影中的单张脸可能只剩几十像素，难以做毛孔/瞳孔高光级别的取证判断。现为每个请求人物额外发送一张原分辨率局部特写。
- **精修重试无法升级模型**：新增 `RefinementRetryRequest.model_alias`（可选），失败或效果不理想时可对单个人物显式指定 `personaweave-refine-plus`/`personaweave-refine-max` 重试，Web Studio 精修弹窗提供对应下拉框；此前重试固定使用网关默认别名，无法触达配置中已有的更强模型。

**本地换脸（对照 FaceFusion 设计技巧修正）**

- **pixel-boost 放大误用 `INTER_AREA` 插值**：`swap_single_face_facefusion` 的对齐裁剪固定使用只适合缩小的 `INTER_AREA`，但 pixel-boost 对中小尺寸人脸是放大而非缩小——按 OpenCV 官方文档，`INTER_AREA` 放大时效果接近 `INTER_NEAREST`，会让送入换脸模型的输入偏糊。现按仿射变换的实际缩放比例动态选择插值方式。
- **合影多人映射为贪心分配，可能张冠李戴**：`process_image_multi_mapping` 此前按 `mappings` 列表顺序让每个映射独立抢占自己的最佳匹配人脸，先处理的映射可能抢走本该属于后一个映射的更强匹配（例如两个目标身份都最像同一张脸时）。新增 `resolve_person_mapping_assignment()`，用匈牙利算法求解全局最优一一分配，阈值过滤在优化前后各做一次以避免"被迫接受"低于阈值的配对。
- **XSeg 遮挡 mask 边缘羽化未随分辨率缩放**：`core/occluder.py` 固定使用 5px 高斯模糊，而 box mask 的羽化是按裁剪宽度比例计算的；大脸特写（pixel-boost 到 1024px）因此比小脸有更硬的遮挡边缘。现按裁剪宽度等比缩放羽化半径。
- **生产路径完全没有色彩校正**：`swap_single_face_facefusion` 此前对贴回前的换脸结果不做任何色彩处理，而 Reinhard 边界环校正代码只存在于未被调用的诊断路径里，源图与目标场景的曝光/白平衡差异会在脸部边缘留下可见色差。现将边界环校正（只修正 mask 边缘窄环，不改变脸部本体色调）接入生产路径，`enable_color_transfer`/`color_transfer_mode` 参数不再是无效占位。

**其他**

- `pyproject.toml`：`httpx` 从 `[dependency-groups].dev` 移入 `[project].dependencies`——`core/ai/gateway.py`、`core/ai/providers/*.py` 在生产代码中依赖它，此前纯生产环境安装（不带 `--dev`）会导致 `ImportError`。

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
- **异步批处理任务管理器** (`api/tasks/batch.py`)：线程池执行、实时进度追踪与 Webhook 回调。
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
  - 新增 `core/ai/arbiter.py`，通过 LiteLLM 接入 Gemini / GPT-4V。
  - 在纯几何特征匹配失效时（大侧脸/背影），利用视觉大模型进行 Chain-of-Thought 推理辅助识别。

---

### 🏗️ 待实施功能 (Planned)

- **Phase 4**: TensorRT FP16/INT8 量化编译、多进程并发 Worker、Docker 全栈镜像打包。
- **视频换脸管线**：MP4/MOV 逐帧检测 + 光流时序平滑（Optical Flow Temporal Smoothing）。
- **BiSeNet 语义保护区域扩展**：当前仅默认保护 `glasses`；模型本身支持的耳饰、项链等其他类别尚未接入可配置的保护区域选择。
