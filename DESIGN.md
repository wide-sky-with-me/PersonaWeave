# PersonaWeave 架构概述

这页给出模块地图、两条图像处理链和数据所有权，适合先建立整体认识。使用与启动看 [README](README.md)，专题按 [文档总览](docs/README.md)选择；具体接口以 [通用契约](api/schemas.py)、[精修契约](api/refinement/contracts.py)及运行中的 `/docs` 为准。

## 模块边界

依赖由界面向外部运行时单向流动：

```text
web/src → HTTP API → api → core → provider / ONNX runtime
```

| 目录/入口 | 主要责任 |
| --- | --- |
| `web/src/components`、`features` | 界面与功能交互；Zustand 管工作区，TanStack Query 管远程状态 |
| `api/app.py`、`api/lifecycle.py` | 应用装配，共享服务初始化与关闭 |
| `api/routes/`、`api/schemas.py` | HTTP 路由与通用请求/响应 |
| `api/tasks/` | 批任务、持久化、跨恢复/精修共享的人物来源规则 |
| `api/projects/` | 编辑工程、媒体副本与显式恢复 |
| `api/portraits/` | SQLite 素材库和幂等导入 |
| `api/refinement/` | 精修会话、轮次/版本、SQLite 事务、事件、制品 I/O 与发布 |
| `api/media.py`、`utils/image_io.py` | 受控图片读取和 Unicode 路径编解码 |
| `core/pipeline.py`、`face_execution.py`、`swapper.py` | 图像/人物编排、单脸执行、模型推理适配 |
| `core/ai/`、`core/refinement/` | AI 配置、网关、结构化契约及整图精修 |
| `api/cli.py`、根目录 `cli.py` | 安装包命令与源码离线入口 |
| `api/portable.py` | Windows CPU/GPU 便携生产入口 |
| `scripts/`、`tests/` | 下载/开发工具、非生产诊断与回归 |

`core` 不反向导入 `api`；包入口不加载模型或创建应用。新增模块、兼容和拆分规则见 [工程架构](docs/engineering-architecture.md)，文档与实现的权威映射见 [文档治理](docs/documentation-governance.md)。

本地文件由 Python 处理路径，OpenCV 只做内存编解码，避免 Windows 中文路径问题。API 读取前仍检查受控目录和像素预算；边界由 `tests/test_package_boundaries.py` 固定。

相机 MPO/MPF JPEG 只取主图，工程保留原始字节；辅助帧不参与检测或聚类。类型分类由 `core/image_security.py::image_mime_type` 统一处理，兼容规则见 [Windows 指南](docs/windows-portable.md)。

准备区的任务操作统一在右侧「换脸控制」，窄屏使用抽屉。`App` 只挂载一个 `useBatchPipeline`，切换工作区或关闭侧栏不卸载任务控制器；具体交互见 [工作台任务控制](docs/engineering-architecture.md#工作台任务控制)。

## 两条图像处理流程

### 本地换脸

```text
解析参考和目标 → 检测与 ArcFace 匹配 → 全局人物分配
→ 单脸对齐与推理 → 保护遮罩交集 → 边界校色与回贴
```

人物分配是一一匹配，不按映射列表贪心占脸。VLM 跨图聚类建议也须通过请求阈值的 ArcFace 复核；106 点提取失败保留默认椭圆保护和诊断。模型适配器只推理，独立单脸执行负责对齐与合成。

结果有可见变化并不代表身份或自然度已达标。模型策略、算法与证据见 [本地换脸](docs/facefusion-local-swap.md)。

### 对话式 AI 精修

```text
冻结原图、基线和人物参考 → 诊断/讨论 → 确认并补充意见
→ 至多一次整图生成 → 建议性复评 → 人工接受或继续下一轮
```

分析使用原图、当前图和身份参考，生成只接收当前完整图与文字。无问题、仅讨论或人物对应不确定时不自动生图。候选直接规范化保存；复评给建议，发布仍由人选择。

查看版、继续编辑的基图和最终接受版相互独立，可从保留的旧版本分支。工作台区分模型原生尺寸与画布尺寸；适配放大不等于模型恢复了细节。

创建和轮次共享累计解码像素预算，超限创建不落库并清理本次临时快照。模型错误只公开白名单状态/超时/格式信息，已保存候选按契约保留。状态机、单图限制和恢复以 [精修契约](docs/ai-refinement.md)为准。

视觉和图像调用统一经 `AIGateway` 进入 LiteLLM 原生 endpoint。角色、别名、deployment 和生成能力只在 [网关 YAML](config/ai_gateway.yaml)定义；供应商协议、环境变量、尺寸与重试费用见 [模型目录](docs/model-catalog.md)。

## 数据与发布

### 人物与分析绑定

会话从原图检测冻结人物编号、空间顺序及可用的脸框中心百分比，不按候选重新排序。

工作台用 `diagnosis_turn_id` 绑定已确认的讨论：必须同会话、同基图、已完成且人物对应明确。未带确认 ID 的显式 API/CLI 生成使用同基图最近已完成分析。后端将结构化诊断与当前意见合成计划；换基图不沿用另一版本的问题。细节见 [精修契约](docs/ai-refinement.md)。

### 哪一份数据说了算

| 数据 | 所有权与边界 |
| --- | --- |
| 批任务状态和结果 | 服务端 SQLite 是事实源；列表先取摘要，打开后取详情，浏览器关联不覆盖状态 |
| 素材库 | `data/portrait_library/library.sqlite3`；对外是不透明 ID，文件读取受目录约束 |
| 精修会话和候选 | `data/refinement_sessions.sqlite3`；图片在 `outputs/refinement_staging/sessions/{session_id}/{digest}.png`，不可变版本保留父子关系 |
| 已接受结果 | `outputs/api_results/refinement/accepted/sessions/`；校验 revision、版本和 digest 后发布同一字节 |
| 旧任务质量证据 | `outputs/refinement_evidence/{task_id}/`，仅为旧档案证据，不替代会话或新发布 |

历史查看不回灌工作区，文件缺失明确降级。[任务历史](docs/task-history.md)定义回看边界；[质量闭环](docs/delivery-quality.md)定义评价与发布的区分，未评价不补成零分。

### 删除与恢复

历史支持批任务、精修会话、旧档案的单条/批量删除。精修删除核对 revision 和空闲状态，提交前暂存所属文件；数据库失败恢复文件，成功后清理客户端关联与缓存。

删除会话覆盖其版本与定稿，不删除原批任务或素材库。旧档案只能清理自己所属的评价和证据，不能跨入新会话目录。事务与数据边界见 [精修接口](docs/refinement-session-api.md)和 [任务历史](docs/task-history.md)。

## 执行与验证

本地模型使用 ONNX CPU/CUDA Provider，可选 AI 不替代本地推理。容量须结合实际图片、人数、模型和硬件测量；启动见 README，摘要与许可见 [模型资产](models/README.md)。

包边界、API 和领域行为分别由对应回归验证；前端执行格式、类型、Vitest 和生产构建。测试通过不代表画质达标；有限样本与离线比较见 [评测说明](docs/swap-benchmarks.md)。

[五人合影实测](docs/refinement-live-test-20260911.md)记录了真实生成与版本衔接，也保留了相貌漂移的限制。历史路线图和退役实现查 Git / [Changelog](CHANGELOG.md)，不作为当前能力清单。

## 专题契约

| 继续了解 | 阅读入口 |
| --- | --- |
| 本地换脸与模型策略 | [本地换脸](docs/facefusion-local-swap.md) |
| 精修状态、分析与生成 | [精修契约](docs/ai-refinement.md)、[分析](docs/refinement-analysis.md)、[生成](docs/refinement-generation.md) |
| 精修界面和 HTTP/存储 | [工作台](docs/refinement-workbench.md)、[接口与存储](docs/refinement-session-api.md) |
| AI 接入、超时与费用 | [模型目录](docs/model-catalog.md) |
| 任务、工程和内存 | [任务历史](docs/task-history.md)、[工程恢复](docs/studio-projects.md)、[内存调度](docs/memory-scheduling.md) |
| 素材与 Windows 交付 | [素材库](docs/portrait-library.md)、[Windows 指南](docs/windows-portable.md) |
| 开发与文档维护 | [工程架构](docs/engineering-architecture.md)、[文档治理](docs/documentation-governance.md) |
