# PersonaWeave

批量图片定点换脸工作台，支持多人物映射、跨图身份匹配和人工确认的整图 AI 精修。

## 当前功能

- **多图内存控制**：上传逐张解码，批任务排队逐张换脸；内存不足时等待并显示原因，超时保留已完成结果。自动精修也按图片串行，融合分块计算保持原分辨率和像素结果。见 [内存与排队](docs/memory-scheduling.md)。

- **操作布局**：右侧“换脸控制”统一映射、启动、AI 首轮开关、重连和批量导出；窄屏点击“换脸控制”打开侧栏。底部横条仅展示状态、进度与耗时。
- **本地换脸**：ONNX 推理、全局人物匹配、遮挡/眼镜保护、手动保护画笔与边界色彩校正。输出仍需人工检查身份和自然度，算法细节见 [本地换脸](docs/facefusion-local-swap.md)。
- **对话式 AI 精修**：以原图检测位置冻结人物定位，先查看折叠分组的问题，确认该版本的分析后填写意见；后端绑定已确认诊断并与人工意见合并成编辑计划，生成后对比定稿；允许无问题或无法判断。视觉整图使用更清晰的有界预览，模型内部采样仍可能限制小脸判断；界面显示原生生成尺寸及放大情况，画布默认完整展示当前版本。生图只接收当前整图与文字，允许必要的整图重绘。每轮至多生成一版，复评只提供建议；可选择旧版本为基图继续编辑，最终由人明确接受。见 [精修契约](docs/ai-refinement.md) 与 [提示词依据](docs/refinement-prompts.md)。
- **素材库**：按身份管理参考图，默认最多选择 8 张非拼图素材；SQLite 持久化，接口只暴露不透明 ID。个人素材包采用自然上镜风格的虚构中国年轻人，男女各 32 位，包含多角度与表情。支持归档人物而保留历史引用，见 [素材库维护](docs/portrait-library.md)。
- **失败提示**：单图、批量、人物分析及精修错误以需手动关闭的弹窗展示具体原因；批量失败按图片汇总，避免轮询重复弹出。
- **Windows 中文路径**：素材、参考图和结果通过统一内存编解码读写，避免 OpenCV 直接打开中文路径失败；旧便携包修复说明见 [Windows 使用说明](docs/windows-portable.md)。
- **工程恢复**：编辑时自动保存素材、画布、人物映射、参数、保护画笔及撤销记录和精修草稿。历史页“恢复工程”可继续编辑，旧记录按实际保留的数据恢复并提示缺失项；查看历史仍不覆盖当前工程。见 [工程恢复契约](docs/task-history.md#complete-studio-projects-and-explicit-recovery)。
- 已保留明确参考素材和逐图人物匹配记录的旧批任务，可恢复精修创建输入，继续使用原本地结果；不会因同批其他图片的角色未出现在当前图而阻止创建，也不会猜测人物对应。
- 工程列表、两类历史和刷新采用同一恢复流程；运行中批任务的精修参考随工程保存，刷新接回原任务后仍可衔接精修。未保存到服务器的修改及已丢失的旧数据不在恢复保证内。
- 接受精修版本后，准备区预览、对比和下载同步使用已接受结果；批量 ZIP 导出当前工程的最终图片，未精修图片保留本地结果，恢复工程后同样可直接导出。
- **任务回看**：持久化历史、只读结果对比和下载；批任务、精修会话与旧版记录支持单条/批量删除，运行中记录受保护。见 [任务历史](docs/task-history.md)。
- **历史任务人工质量评价**：评价与发布独立，未评价时成片率为未知；不作为新精修会话的自动门禁。见 [质量闭环](docs/delivery-quality.md)。

## 本地启动

Windows 用户可使用 [Windows x64 便携版](docs/windows-portable.md)：提供 CPU 和 NVIDIA GPU 完整包，解压后双击 `Start.cmd`，内置运行时、前端和本地模型；已有 CPU 版可使用较小的 GPU 升级包。AI 服务首次通过 `Configure-AI.cmd` 配置。

以下源码开发启动需要 Python 3.11+、uv、Node.js 和 pnpm。当前项目锁定依赖面向 Linux/CUDA 环境；Windows 便携构建分别安装 CPU/CUDA runtime，不改变开发环境依赖，不承诺固定吞吐量。

```bash
uv sync --dev
pnpm --dir web install
uv run python scripts/download_models.py
cp .env.example .env
uv run python scripts/dev.py
```

已有 `.env` 时直接编辑，不要覆盖。模型下载器校验 SHA-256；所需资产、来源和许可证见 [模型说明](models/README.md)。启动脚本只预检并运行服务，不自动安装依赖或下载权重。

- Web Studio：<http://localhost:3000>
- API 文档：<http://localhost:8000/docs>（请求/响应以运行中的 OpenAPI、[通用契约](api/schemas.py) 和 [精修会话契约](api/refinement/contracts.py) 为准）
- 健康检查：`GET /health` 或 `GET /api/v1/health`

可选 AI 服务统一经 LiteLLM Router 接入，不绑定服务商。在本机 `.env` 分别填写视觉服务的 `AI_VISION_MODEL`、`AI_VISION_API_BASE`、`AI_VISION_API_KEY`，以及图像编辑服务的 `AI_IMAGE_MODEL`、`AI_IMAGE_API_BASE`、`AI_IMAGE_API_KEY`。`.env.example` 中 `openai/gpt-4o`、`openai/gpt-image-1` 仅是可替换模型示例，URL 与 Key 由用户自行设置；不要提交实际凭据。模型别名、角色、生成策略只在 [网关配置](config/ai_gateway.yaml) 声明，`PERSONAWEAVE_AI_CONFIG` 可覆盖 YAML 路径。缺少或无效配置只禁用可选 AI，模型目录及新精修轮次命令返回 503；本地换脸、已存会话查看和人工接受仍可用。独立仲裁接口的建议性失败响应见 [模型目录](docs/model-catalog.md)，不能将 HTTP 200 当作身份匹配成功。健康检查不代表上游鉴权成功；OpenAI 兼容服务的模型名应填写 `openai/实际模型名`，避免依赖在线模型目录推断协议；精修失败会区分本地路由配置错误、上游 HTTP 错误、超时和结构化输出格式问题，不展示凭据或原始错误内容。

默认图像尺寸策略为 `size: auto`，交给 provider 原生处理，不承诺 4K。视觉与图像服务可分开配置；更换 URL 并不代表目标服务支持图像编辑，需核实 LiteLLM 对该 provider 的 image-edit endpoint 支持。配置示例、尺寸策略、多 deployment、重试成本与旧配置迁移步骤见 [模型目录](docs/model-catalog.md)。

手动启动时，在两个终端分别执行：

```bash
uv run uvicorn api.app:app --host 0.0.0.0 --port 8000
pnpm --dir web dev --host 0.0.0.0 --port 3000 --strictPort
```

预检失败先检查模型和端口；运行 `uv run python -c "import onnxruntime as o; print(o.get_available_providers())"` 查看执行后端。`Ctrl+C` 会停止一键启动的两个服务。

## Docker 部署

```bash
# 首次部署先从 .env.example 创建 .env
docker compose up --build -d
docker compose logs -f
```

容器同源提供工作台和 API：<http://localhost:8000>、<http://localhost:8000/docs>。缺少必需模型时入口脚本自动下载并校验；`models/`、`data/`、`outputs/` 由卷持久化。`docker compose down` 停止服务，代码更新后重新构建。

默认使用 CPU；启用 GPU 需宿主机具备 NVIDIA Container Toolkit，并开启 [docker-compose.yml](docker-compose.yml) 中的 GPU 设备配置。部署实现见 [Dockerfile](Dockerfile) 和 [容器入口](docker-entrypoint.sh)。

## 素材导入与离线命令

```bash
uv run personaweave --help
uv run personaweave-import-portraits --help
```

素材导入读取来源目录中的 `manifest.csv`，按内容哈希去重，损坏图片进入 `quarantine/`。目录位于 `data/portrait_library/`，导入格式以 [importer.py](api/portraits/importer.py) 为准。根目录 `cli.py` 也保留为离线源码入口。

## 开发与验证

```bash
uv run ruff format .
uv run ruff check .
uv run pytest
pnpm --dir web format
pnpm --dir web lint
pnpm --dir web test
pnpm --dir web build
```

## 文档导航

| 目的 | 文档 |
| --- | --- |
| 理解系统与存储边界 | [架构概述](DESIGN.md) |
| 修改代码、拆分模块或清理实现 | [工程架构](docs/engineering-architecture.md)、[Agent 规范](AGENTS.md) |
| 核对文档与实现的事实源 | [文档治理](docs/documentation-governance.md) |
| 运行离线评测、查阅有限样本实测 | [评测说明](docs/swap-benchmarks.md) |
| 核对测试图片与模型资产 | [测试素材](tests/fixtures/README.md)、[模型说明](models/README.md) |
| 查阅历史变化 | [Changelog](CHANGELOG.md)（历史记录，不作为当前能力清单） |

工作台在窄屏保留文字导航；画布顶部切换视图与定位人物，下方统一进入当前图 AI 精修或下载结果。角色映射决定实际换脸对象。键盘图标可打开快捷键说明；切换工作区或打开弹窗时不会误操作隐藏的画布。历史加载失败会单独提示，不显示为“暂无记录”。

实测边界：[五人合影两轮精修记录](docs/refinement-live-test-20260911.md) 验证了真实生成和版本衔接，但候选出现相貌漂移及小遮挡细节错误，未定稿；不能据流程通过宣称画质达标。

AI 网关默认等待上限为 5 分钟，精修分析/复评外层额外预留 30 秒处理证据与响应；图像编辑仍使用独立超时。配置见 [模型目录](docs/model-catalog.md#routing-retries-and-fallbacks)。

工程保存会分别归档素材库参考原图和界面预览缩略图，修复预览地址被误认为本地路径而返回 422 的问题；恢复后的图片独立于原素材库。路径校验与回归见 [工程历史契约](docs/task-history.md)、`api/projects/recovery.py` 和 `tests/test_projects.py`。

Windows 跨机器排查可使用只读文件核验及临时工程自检，原始交付包可用按清单匹配的源码修复包更新；配置好的目录迁移需同时保留素材库、工程数据库与结果文件。见 [Windows 复制与排查](docs/windows-portable.md#跨机器复制与-422-排查)。

Windows 便携包的 VC++ 五组件清单已补齐 ONNX Runtime 所需 `msvcp140_1.dll`，旧包可使用 `Repair-Runtime.cmd` 更新应用运行库；构建前校验与原生依赖证据见 [Windows 运行库核查](docs/windows-portable.md#2026-09-23-vc-漏项核查与修复)。
