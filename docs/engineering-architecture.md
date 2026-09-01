# 工程架构与 AI 开发约束

本文是 PersonaWeave 工程边界、代码归属和兼容策略的权威说明。它不重复具体换脸或 AI 精修算法；这些行为分别以 [facefusion-local-swap.md](./facefusion-local-swap.md) 和 [ai-refinement.md](./ai-refinement.md) 为准。

## 1. 依赖方向

```text
Web Studio (web/src)
        ↓ HTTP contracts
API / orchestration (api)
        ↓ domain calls
Core algorithms and provider boundaries (core)
        ↓
ONNX Runtime / LiteLLM / DMX protocols
```

- `web/src/components` 负责渲染和局部交互；跨组件工作区状态归 Zustand，服务端异步状态归 TanStack Query，纯计算归 `web/src/utils`，可复用的有状态交互归 `web/src/hooks`。
- `api` 负责 HTTP 契约、任务编排、持久化和发布边界，不实现人脸算法或供应商协议。
- `core` 负责可独立调用的领域算法、模型推理和 AI provider 适配，不得反向导入 `api`。
- `core/providers` 只适配供应商协议；业务选择使用稳定别名，不直接认识供应商 SDK。
- `core/__init__.py` 与 `api/__init__.py` 必须无副作用。具体类型从定义模块导入，避免读取 schema 时启动 FastAPI，或读取 AI 网关时加载 ONNX/InsightFace。该边界由 `tests/test_package_boundaries.py` 验证。

## 2. 生产、诊断与退役代码

代码必须能被归入以下一种状态：

| 状态 | 允许位置 | 要求 |
| --- | --- | --- |
| 生产 | `core/`、`api/`、`web/src/` | 必须从当前 API/CLI/UI 入口可达，并有对应行为测试和现行文档 |
| 诊断/评测 | `scripts/` | 必须明确标注不属于生产路径；模型与别名从生产配置读取，不复制清单 |
| 退役 | Git 历史、明确标记的历史 Changelog | 不得继续保留生产导出、启动检查、下载依赖或仅为其存在而保留的测试 |

本次里程碑审计删除了已退出整图精修契约的 `core/localized_refinement.py`，以及仅被旧诊断脚本调用的 CodeFormer/GFPGAN `core/enhancer.py` 和三个脚本。现行精修禁止局部候选合成；现行本地换脸也禁止全脸修复，因此继续保留这些实现会制造一条看似可用但与生产契约冲突的“影子架构”。历史原因仍可在 `CHANGELOG.md` 中查阅，但历史文字不是可达性证据。

## 3. 单一事实源

| 事实 | 唯一可执行来源 | 消费方式 |
| --- | --- | --- |
| VLM 模型与角色 | `config/ai_gateway.yaml` 的 `model_list` / `vision_roles` | `AIGateway.vision_alias()` |
| 图像模型、默认项与生成尺寸能力 | 同一 YAML 的 `image_models` / `default_image_alias` / `generation` | 后端目录 API、`RefinementService`、前端动态列表、评测脚本 |
| 本地模型下载源与摘要 | `scripts/download_models.py` 的 `MODEL_URLS` / `MODEL_SHA256` | 下载后强制 SHA-256 校验 |
| API 请求/响应 | `api/schemas.py` | FastAPI response/request model 与前端对应类型 |
| 批任务状态 | SQLite 持久化快照 | 摘要列表后按 ID 获取详情；浏览器状态不能覆盖服务端事实 |

前端不得维护模型别名排序表；只把服务端 `default_alias` 放在首位，其余保持配置顺序。模型分辨率不能通过别名字符串判断。新增模型若缺少 `generation` 能力声明，网关必须启动失败，而不是套用一个静默默认值。对应证据为 `tests/test_ai_gateway_config.py`、`tests/test_refinement_service.py` 和 `tests/test_documentation_consistency.py`。

## 4. 模块拆分准则

拆分依据是“变化原因”，不是任意行数：

- 纯展示、算法计算、外部 I/O、任务状态机和持久化分别演进时，应放在不同模块。
- 同一状态机的连续步骤留在一个编排器中；不要为了缩短文件制造多层一行转发。
- `TargetCanvas` 的保护画笔状态已移入 `useManualProtectionBrush`，因为画布 mask 序列化可独立变化；`RefinementModal` 的人脸缩略图和复评报告已移入 `RefinementReviewPanels`，因为展示变化不应触碰任务编排。
- 新抽象至少应消除一份重复事实、隔离一种副作用或形成可单测的稳定边界；否则优先保留直观函数。
- Yaw、参考图质量、输出可见变化和极端侧脸兼容性集中在纯领域模块 `core/face_metrics.py`；API、流水线和诊断脚本不得再从编排器中绕行导入这些规则。

## 5. 兼容与删除策略

兼容分支必须同时具备：明确调用方、回归测试、弃用说明和删除条件。缺少这些证据的“也许有人用”分支不得永久存在。

- 旧 `GET /api/v1/batch-tasks` 有既有客户端风险，因此作为 deprecated 接口保留；新代码只使用任务历史摘要接口。
- 旧持久化精修状态 `LOCALIZING` 只为读取已落盘任务保留，新任务不得产生。
- AI Gateway 的旧单模型 `image_edit` 配置没有现存配置或调用方，已删除；当前只接受 `image_models` 目录。

删除前必须用入口引用、测试引用、文档契约和运行资产四方面交叉核实。删除后同步清理导出、下载器、启动预检、测试和现行文档，避免“死实现没了，死配置还在”。

## 6. 硬编码判断

并非所有常量都是问题。算法阈值可以靠近算法并命名、注释和测试；跨层事实、供应商能力、模型目录、状态集合和用户可见默认值必须有单一来源。

批任务耗时不再使用固定“每图 0.35 秒”。`TaskManager` 从近期成功/部分成功结果的真实 `cost_ms` 中取中位数估算；没有样本时返回 `0` 表示暂无可靠估计。不能为了界面看起来完整而伪造精确数字。

## 7. 变更完成标准

一次工程变更只有在以下条件同时满足时才算完成：

1. 生产入口、数据边界和失败语义未被无意改变；有意改变已更新 schema 与权威文档。
2. 删除项的导出、下载、启动检查和仅服务旧实现的测试已一并处理。
3. 配置和文档之间的高风险重复事实具有机械一致性测试。
4. Python 通过 Ruff 与完整 pytest；Web 通过 TypeScript 检查、完整 Vitest 和生产构建。
5. 没有临时输出、测试密钥、真实路径或大模型资产被误纳入源码交付。

文档事实治理规则见 [documentation-governance.md](./documentation-governance.md)。
