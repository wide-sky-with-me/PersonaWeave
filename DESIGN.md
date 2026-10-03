# PersonaWeave 架构概述

本文说明现行模块和数据流；操作步骤见 [README](README.md)，实现与测试的权威映射见 [文档治理](docs/documentation-governance.md)。详细接口由 [通用契约](api/schemas.py)、[精修会话契约](api/refinement/contracts.py) 和运行中的 `/docs` 提供，不在概述重复维护 JSON 示例或模型目录。

## 模块边界

```text
web/src → HTTP API → api → core → provider / ONNX runtime
```

| 目录/入口 | 职责 |
| --- | --- |
| `web/src/components`、`features` | 工作台展示、精修与历史功能；Zustand 管理工作区，TanStack Query 管理远程状态 |
| `api/app.py`、`api/lifecycle.py` | 应用装配及共享服务生命周期 |
| `api/routes/`、`api/schemas.py` | HTTP 路由和请求/响应契约 |
| `api/tasks/`、`api/media.py` | 本地批任务、持久化与受控图片加载 |
| `api/refinement/` | 新精修会话、turn/version 契约、SQLite、事件及受控发布；`legacy.py` / `legacy_quality.py` 保留旧档案与人工质量评价 |
| `api/portraits/` | SQLite 素材库及幂等导入 |
| `api/cli.py` | 离线换脸命令行；根目录 `cli.py` 为源码启动器 |
| `api/portable.py` | Windows CPU/GPU 便携入口：本机单实例、浏览器启动及本地 AI 配置 |
| `core/pipeline.py`、`core/swapper.py` | 本地换脸编排与模型推理适配器 |
| `core/ai/`、`core/refinement/` | AI 配置、契约、协议与整图精修服务 |
| `scripts/`、`tests/` | 下载/开发工具、非生产诊断与回归验证 |

`core` 不反向导入 `api`，包入口不加载模型或创建应用。拆分、兼容与测试规则见 [工程架构](docs/engineering-architecture.md)。

本地图片读写统一使用 `utils/image_io.py`：Python 负责 Unicode 路径文件访问，OpenCV 负责内存编解码；API 仍须先完成受控路径鉴权与像素限制。素材引用、CLI 和批任务结果不直接调用 OpenCV 路径读写接口，边界由 `tests/test_package_boundaries.py` 约束。

受控图片解码将 Pillow 检出的 MPO 视为 JPEG 兼容主图，统一通过 `core/image_security.py::image_mime_type` 分类；
工程媒体回看复用相同 MIME 规则并保留原始字节。辅助帧不进入检测/聚类，既有编码体积、主图像素与内存保护不变；
相机文件兼容契约与回归见 [Windows 使用说明](docs/windows-portable.md)。

准备工作区的任务操作集中在右侧“换脸控制”，常驻场景推荐预设与特征融合主滑块，高级算法调校（推理模型、余弦截断阈值 $\tau$、光影色温适配）由渐进折叠面板收纳并动态透出摘要；窄屏由抽屉承载；顶部准备标签及底部状态栏仅负责展示。`App` 唯一挂载 `useBatchPipeline` 管理批任务提交、轮询与导出，侧栏通过显式属性调用，关闭抽屉或切换工作区不影响任务恢复。见 [工程架构](docs/engineering-architecture.md)。

## 两条图像处理流程

**本地换脸**：解析参考素材与目标 → 检测及 ArcFace 身份匹配 → 全局一一分配人物 → 对齐与 `infer_aligned()` 推理 → 保护遮罩交集、边界校色与回贴。跨图 VLM 聚类建议仍须按请求阈值通过 ArcFace 复核；106 点提取异常时保留默认椭圆保护及失败诊断。适配器只推理，对齐和合成由流水线统一负责；仅通过可见变化检查不等于身份或自然度验收。细节、模型策略与证据见 [本地换脸](docs/facefusion-local-swap.md)。

**对话式 AI 精修**：冻结原图、当前基线和按稳定人物 ID 绑定的参考 → 提高有界整图证据清晰度的三证据 VLM 诊断/讨论 → 按后端 mode 执行至多一次单图生成 → 直接保留规范化候选 → VLM 建议性复评 → 人工选择接受或开启新轮次。创建与轮次执行共用累计解码像素预算，超限创建不落库并清理本次临时快照。模型失败仅公开上游状态码、超时或白名单格式校验类型，保留候选的失败语义不变。无问题、仅讨论或人物对应不确定时不自动生图；可显式从旧版本分支，查看版、修改基图和最终接受版独立。画布默认完整查看选中版，可显式开启原图/初稿对比；工作台同时展示模型原生输出尺寸与画布尺寸，避免将适配放大误认成细节恢复。状态机、单图约束、SSE 恢复及发布以 [精修契约](docs/ai-refinement.md) 为唯一说明。

VLM 和图像模型访问均经过 `AIGateway`，分别调用 LiteLLM `Router.acompletion` 与 `Router.aimage_edit`。原生 `model_list` 同时声明两类 deployment；稳定 `model_name` 与实际模型、连接参数解耦，允许同别名多 deployment。`image_models` 仅保存引用同一目录的展示与 `generation` metadata，不另建 provider/params 路由；角色、别名与生成能力来自 [config/ai_gateway.yaml](config/ai_gateway.yaml)。目录 API 保持原响应形状，provider 标记为 `litellm`，不暴露 URL 或凭据，前端无需另建服务配置。

连接与模型通过通用环境变量分开配置视觉/图像服务；默认不自动重试付费请求，不伪造免费定价，不按模型名猜分辨率。供应商协议委托 LiteLLM 原生 endpoint，禁止手写供应商 HTTP 或失败时切回私有协议。尺寸、同能力 fallback、迁移与支持边界见 [模型目录](docs/model-catalog.md)。

## 数据与发布

交互会话从执行时的原图检测冻结人物编号、空间顺序及可用的脸框中心百分比，不按候选图重新排序。交互式精修工作台依次提供分组检查问题、确认分析、补充意见与生成、对比并继续下一轮。生成命令通过 diagnosis_turn_id 绑定人工确认的同会话、同基图、已完成且人物明确的讨论；未带绑定的显式 API/CLI 生成仍使用同基图最近已完成的分析。后端将结构化诊断独立于历史摘要传给视觉模型，与当前人工意见合并成计划，再连同当前整图交给生图模型。换基图不沿用其他版本的问题；旧轮次折叠、图片入口去重，接受和导出集中到画布旁。实现与回归见 [精修契约](docs/ai-refinement.md)。

| 数据 | 所有权与边界 |
| --- | --- |
| 批任务状态与结果 | 服务端 SQLite 为事实源，历史列表取轻量摘要，打开任务后取详情；浏览器关联不能覆盖服务端状态 |
| 素材库 | `data/portrait_library/library.sqlite3`；接口使用不透明 ID，文件访问校验目录边界 |
| 精修会话、证据与候选 | `data/refinement_sessions.sqlite3` 为事实源；图片在 `outputs/refinement_staging/sessions/{session_id}/{digest}.png`，不可变版本保留父子关系 |
| 已接受会话结果 | `outputs/api_results/refinement/accepted/sessions/`；显式接受校验会话 revision、版本和 digest 后发布相应字节；旧档案保持原目录 |
| 旧任务人工质量证据 | `outputs/refinement_evidence/{task_id}/`；仅旧档案的只读评价证据，不作为新会话状态或发布替代 |

历史查看不回灌工作区，文件缺失明确降级；见 [任务历史](docs/task-history.md)。人工评价与接受独立，未知数据不补成零值，见 [质量闭环](docs/delivery-quality.md)。

历史页统一提供批任务、精修会话及旧版档案的单条/批量删除。精修删除核对 revision 与空闲状态，事务提交前私下暂存所属文件，数据库失败恢复；成功后清理客户端选中状态和详情缓存。会话删除覆盖其全部版本与定稿，保留原批任务和素材库。旧档案按任务归属清理评价和证据，不能跨入新会话目录。边界与回归见 [任务历史](docs/task-history.md) 和 [精修契约](docs/ai-refinement.md)。

## 执行与验证

本地模型使用 ONNX CPU/CUDA Provider，AI 服务可选；容量应以实际图片、人数、模型和硬件测量。开发启动和 Docker 入口见 [README](README.md)。模型摘要及许可证见 [模型资产](models/README.md)。

边界由 `tests/test_package_boundaries.py` 验证，任务/精修行为由 API 与领域回归覆盖；前端执行类型检查、Vitest 和生产构建。离线算法比较与历史实测集中在 [评测说明](docs/swap-benchmarks.md)，测试通过不代表画质达标。旧路线图和退役实现只在 Git / [Changelog](CHANGELOG.md) 中查阅。

## 专题契约

当前行为由各专题文档定义，此处不重复：

- 本地换脸、模型与许可：[facefusion-local-swap.md](docs/facefusion-local-swap.md)
- 对话式整图精修：[ai-refinement.md](docs/ai-refinement.md)，实测边界见 [refinement-live-test-20260911.md](docs/refinement-live-test-20260911.md)（真实生成与版本衔接已验证，但候选出现相貌漂移，不能据流程通过宣称画质达标）
- AI 网关、超时、重试与降级：[model-catalog.md](docs/model-catalog.md)
- 批任务、工程与历史：[task-history.md](docs/task-history.md)；内存与排队：[memory-scheduling.md](docs/memory-scheduling.md)
- 素材库：[portrait-library.md](docs/portrait-library.md)；Windows 便携版：[windows-portable.md](docs/windows-portable.md)
- 工程边界与清理：[engineering-architecture.md](docs/engineering-architecture.md)；文档分层与治理：[documentation-governance.md](docs/documentation-governance.md)
