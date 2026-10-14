# 工程架构与开发边界

本文规定依赖方向、模块拆分和兼容策略。目录速览见 [架构概述](../DESIGN.md)，业务约束见 [本地换脸](facefusion-local-swap.md)、[整图精修](ai-refinement.md)。

## 依赖与职责

依赖单向流动：`web/src → api → core → provider/runtime`。

- Web 组件负责展示及局部交互，Zustand 管工作区，TanStack Query 管远程状态。精修和历史归 `features/`，测试随功能放置；跨目录使用 `@/`，同目录使用相对引用。
- `api/app.py` 装配中间件、路由和静态站点，`lifecycle.py` 创建/关闭共享服务。路由从 `request.app.state` 读取服务，可选 AI 服务通过 `dependencies.py` 访问。
- `api/portable.py` 是 Windows CPU/GPU 便携生产入口，复用现有应用和生命周期；`scripts/build_windows_portable.py` 负责固定依赖及资产打包，`build_windows_gpu_upgrade.py` 与 `upgrade_windows_gpu.py` 是差分交付工具，不引入另一条推理路径。升级只修改清单内程序文件，持有同一运行锁，校验全部载荷后逐文件原子替换、最后发布 manifest；中断允许从旧/新摘要混合状态重试。交付契约见 [Windows 便携版](windows-portable.md)。
- 本地批任务归 `api/tasks/`，人物来源、已分配结果和跳过原因由 `provenance.py` 供批任务、精修接续与工程恢复共享，不由恢复模块反向依赖精修编排。对话会话的状态、SQLite 与事件归 `api/refinement/store.py`，内容寻址及受控文件 I/O 归同域 `artifacts.py`。跨功能图片 I/O 归 `api/media.py`，素材库与导入归 `api/portraits/`；领域模块不得反向导入路由或应用入口。
- `core` 不导入 `api`。`core/__init__.py` 与 `api/__init__.py` 无副作用，不重新导出模型、不创建应用或设置运行状态。
- `core/ai/configuration.py` 和 `types.py` 不加载 LiteLLM、provider 或推理模型；业务通过 `AIGateway` 访问，供应商协议交由 LiteLLM 原生 Router endpoint，禁止仓内手写供应商 HTTP 或私有协议回退。视觉使用 `Router.acompletion`，图像使用 `Router.aimage_edit`（LiteLLM 1.98.0 在实例上动态注册该方法，不得仅检查类属性就判定不支持）。
- `core/swapper.py` 只做 `infer_aligned()` 推理并声明共享对齐模板；对齐、保护遮罩、校色和回贴由 `core/face_execution.py` 单脸执行边界完成。`pipeline.py` 保留模型缓存与锁、检测、全局人物分配及逐脸编排；纯姿态/质量规则归 `core/face_metrics.py`。

## 工作台任务控制

准备页的执行操作集中在右侧 `ParameterPanel`，窄屏使用「换脸控制」抽屉。按职责区分：

- **参数与执行**：侧栏负责角色映射、单图/批量换脸、AI 首轮授权、任务重连与 ZIP 导出。场景预设只陈述参数；符合推荐值时显示「推荐默认」，依据见 [预设文案决策](../.agents/notes/frontend/20261004-preset-copy-states-parameters.md)。高级调校折叠展示，并提供动态摘要。
- **画布指派**：人脸编号留在图内，点击后打开 `FaceAssignPopover`；浮窗通过 portal 渲染并夹紧到视口，窄屏用底部抽屉，保留键盘访问。依据见 [指派浮窗决策](../.agents/notes/frontend/20261004-face-assign-popover-portal.md)。
- **导航与反馈**：精修页头部可返回准备画布；顶部准备项只显示状态。底部 `BatchStatusBar` 展示进度、状态与耗时，不承载按钮或开关。

`App` 唯一挂载 [`useBatchPipeline`](../web/src/hooks/useBatchPipeline.ts)，负责批量提交、服务端轮询、已关联任务恢复与导出；通过属性将同一控制器交给桌面/抽屉侧栏和状态栏，不使用窗口自定义事件转发。关闭侧栏或切换工作区不卸载控制器；提交前同步读取工作区运行状态，避免重复请求。AI 首轮授权默认关闭，单张目标也可显式授权走批任务接续精修；单图试换仍仅执行本地换脸。侧栏打开映射对话框时关闭抽屉，避免遮挡。

回归见 [`BatchControls.test.tsx`](../web/src/components/studio/BatchControls.test.tsx) 与 [`ResponsiveWorkflowAccess.test.tsx`](../web/src/components/layout/ResponsiveWorkflowAccess.test.tsx)。

## 拆分与单一事实源

按变化原因拆分展示、纯计算、外部 I/O、状态机和持久化。同一状态机的连续步骤保留在一个编排器，不增加多层单行转发。抽象须消除重复事实、隔离副作用或形成可独立测试的稳定边界，否则保留直观函数。

精修各层分工如下：

- `api/refinement/store.py` 管会话事实、读取门面、事务与恢复决定；延迟图片加载仍先经 store 校验归属。
- `api/refinement/service.py` 保留连续执行步骤；同域 `contracts.py` 定义契约，`batch.py` 处理批量接续。
- `artifacts.py` 只处理路径、已校验字节与磁盘副作用，不判定数据库归属。
- `core/refinement/conversation.py` 负责三证据诊断、单图生成和建议性复评，不访问 API、SQLite 或发布目录；`image_ops.py` 复用纯尺寸/编码规则。
- 前端区分查看版、修改基图与已接受版。SSE 只通知快照失效，浏览器不是状态机；保护画笔集中于 `useManualProtectionBrush`。

精修查询键由无运行依赖的 [`queryKeys.ts`](../web/src/features/refinement/queryKeys.ts) 维护，工作台、接受投影、SSE、删除与历史复用同一来源；历史列表不为读取键值而导入工作台 Hook。缓存键形状和分页失效范围由 [就近回归](../web/src/features/refinement/queryKeys.test.ts) 固定。

| 事实 | 可执行来源 |
| --- | --- |
| AI 角色、别名、默认模型和生成尺寸能力 | `config/ai_gateway.yaml` |
| 模型下载来源及摘要 | `scripts/download_models.py` |
| 请求/响应契约 | 通用 `api/schemas.py`；精修 `api/refinement/contracts.py`；模型输出与共享输入预算 `core/refinement/contracts.py` |
| 任务状态 | 服务端 SQLite 快照 |
| 运行耗时估计 | 已完成任务的真实 `cost_ms`；样本不足时返回 `0` 表示暂无可靠估计 |

AI 配置遵循以下边界，完整说明见 [模型目录](model-catalog.md)：

- `model_list` 是视觉与图像 deployment 的唯一连接/模型来源，稳定别名可关联多个 deployment。
- `image_models` 只含 `model_name`、`display_name`、`description`、`generation`，不复制 `provider`/`params` 或路由；业务不按别名猜能力。
- `generation` 三选一：`size: auto`、离散 `sizes`、动态 `max_side/min_side/grid/max_pixels?/delimiter`。缺失或冲突使配置验证失败并禁用可选 AI，不阻断本地换脸、已存会话或档案访问。
- 默认 `size: auto` 不承诺 4K。fallback 须同能力，图像还须同 generation；默认 Router `num_retries: 0`、图像 `max_retries: 0`，显式增加重试意味着重复付费风险。
- 前端只将服务端默认模型置顶，其余保持配置顺序，不另建可用性或排序表。

算法常量可靠近实现，但须有注释与测试；未经实测不写精确性能值。

## 生产、诊断与退役

| 分类 | 要求 |
| --- | --- |
| 生产 | 从当前 API/CLI/UI 入口可达，有行为测试与现行契约 |
| 诊断/评测 | 放在 `scripts/` 并明确标注非生产；复用配置和请求模型，产物保存在独立诊断目录 |
| 退役 | 只留 Git 或明确标记的 Changelog；清理源码、导出、启动检查、下载依赖和专属测试 |

删除前交叉核对生产入口、测试、文档和运行资产，不能仅凭文件名或缺少 import 判死。独立 CLI 也是入口：根目录 `cli.py` 是受支持源码启动器；保留的诊断脚本必须复用现行契约，不能为旧测试恢复退役生产路径。禁止保留与现行契约冲突的备用流水线。

兼容分支须具备明确调用方、回归测试、弃用说明和删除条件；不为未知旧实现永久增加 `hasattr` 或宽泛异常兜底。旧完整批任务列表仍 deprecated，新代码只读摘要；旧精修仅 `api/refinement/legacy.py` 与 `api/routes/legacy_refinement.py` 保留档案读取、质量评价及人工接受，旧生成/计划/诊断/取消/重试返回 410。删除档案边界须先作出历史保留/迁移决定；旧 LOCALIZING 等状态只解释历史数据，不得混入新 turn。具体契约见 [任务历史](task-history.md) 和 [精修契约](ai-refinement.md)。

精修与 legacy 的删除文件暂存/失败恢复共用 `api/refinement/deletion.py`，具体记录状态和 SQL 删除仍由各自 store/archive 管理。前端选择删除的逐条结果归 `web/src/hooks/useRecordDeletion.ts`，精修缓存/轻量关联清理由 `features/refinement/sessionCache.ts` 管理；它只清除失效关联，不回灌旧图片、映射或画笔。

## 打包与验证

`pyproject.toml` 声明顶层 `api` / `core` / `utils` 的打包位置；CLI 从安装包解析。内部职责提取保持源码、wheel、便携及公开文档入口兼容，不另设转发目录树。模型、AI YAML 和本机运行数据不随 wheel 打包，部署须显式提供资产；包入口不通过重新导出掩盖失效引用。取舍见 [领域提取决策](../.agents/notes/platform/20261014-domain-extraction-with-stable-entrypoints.md)，本轮验证见 [分支验收](validation/20261014-organization-branch.md)。

Python 使用 uv + Ruff，前端使用 pnpm + 固定版本 Prettier。前端格式为 2 空格、单引号、120 字符；构建产物和锁文件不参与格式化。运行命令见 [README](../README.md#开发与验证)。CI 的 Runner、动作与应用工具链以 [workflow](../.github/workflows/ci.yml)为准；固定基线和动作提交的理由见 [CI 决策](../.agents/notes/platform/20261014-ci-runner-baseline-and-action-pins.md)。

- `tests/test_package_boundaries.py`：包入口、纯配置隔离及禁止反向依赖。
- `tests/test_facefusion_pipeline.py`、`tests/test_swap_diagnostics.py`：当前换脸、保护遮罩与回贴。
- `tests/test_ai_gateway_config.py`、`tests/test_download_models.py`：配置与资产契约。
- `tests/test_documentation_consistency.py`：高风险重复事实、文档链接与章节引用。

行为变更须同步权威文档、摘要和 Changelog，执行 Ruff、相关 Python 回归；跨模块重构执行完整 pytest，前端变更执行类型检查、Vitest 和构建。文档-only 变更执行文档一致性检查；修改 Python 测试时仍执行 Ruff。交付前清理临时文件。证据要求见 [文档治理](documentation-governance.md)。

FastAPI `TestClient` 回归的执行环境须允许 asyncio socketpair 跨线程唤醒；启动停滞时先用有时限的空应用及线程栈定位，再对照运行同一测试。依据见 [测试环境决策](../.agents/notes/platform/20261010-testclient-requires-socketpair-wakeup.md)。

## 界面交互与可视验证

界面交互按操作、导航与执行状态分别约束：

- **图像操作**：视图工具和身份定位在画布顶部，角色映射决定实际换脸对象。精修/下载各有固定入口；结果操作栏占独立空间，不覆盖图片。
- **窄屏导航**：保留导航文字，工具不挤成竖排；工作区使用动态视口高度。
- **快捷键隔离**：Radix 说明对话框负责焦点约束、Escape 和焦点恢复。准备页隐藏或对话框打开时暂停画布快捷键，窗口失焦结束临时原图对比。
- **执行中操作**：锁定右栏参数和目标图移除。同步单图请求没有真实进度时只显示等待状态。

历史加载、错误与空列表分别展示；列表/详情共用中文状态标签，列表日期采用中文格式，批量选择支持半选并在删除时锁定。精修空页面提供回到准备与选择已有图片的路径。CSS 和 Framer Motion 遵循系统减少动态效果偏好。

生产实现见 [`CanvasTopToolbar`](../web/src/components/studio/CanvasTopToolbar.tsx)、[`CanvasResultActionBar`](../web/src/components/studio/CanvasResultActionBar.tsx)、[`useStudioShortcuts`](../web/src/hooks/useStudioShortcuts.ts)；快捷键隔离和操作入口回归随组件/Hook 测试维护。非生产浏览器脚本 [`smoke_studio_ui.py`](../scripts/smoke_studio_ui.py) 使用固定图片与拦截接口，覆盖准备、结果、历史、精修的桌面/窄屏截图与几何检查，不将模拟响应当成真实模型能力验证。

## 编辑工程持久化边界

`api/projects/` 隔离工程 SQLite、图片快照与旧记录恢复；`api/routes/projects.py` 只装配 HTTP。`web/src/features/projects/` 隔离序列化媒体、自动保存与显式恢复，组件负责保存反馈和历史入口。工程只持久化编辑输入、结果副本和轻量精修选择，不成为任务/接受状态的第二事实源。先完整校验后更新 Zustand，原图拓扑与人物编号保留；既有只读历史测试仍有效。细节与证据见 [工程契约](studio-projects.md)。
