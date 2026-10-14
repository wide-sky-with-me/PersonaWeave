# PersonaWeave

批量图片定点换脸工作台：准备人物参考、指定目标人物、本地换脸，再按需进行人工确认的整图 AI 精修。

[![CI](https://github.com/wide-sky-with-me/PersonaWeave/actions/workflows/ci.yml/badge.svg)](https://github.com/wide-sky-with-me/PersonaWeave/actions/workflows/ci.yml)

[文档总览](docs/README.md) · [公开说明与样例](docs/public-project.md) · [Wiki](https://github.com/wide-sky-with-me/PersonaWeave/wiki) · [参与贡献](CONTRIBUTING.md) · [漏洞报告](SECURITY.md) · [发行附件](https://github.com/wide-sky-with-me/PersonaWeave/releases)

**先选运行方式**：源码开发见下方「本地启动」，CPU/ARM64 容器见 [Docker 指南](docs/docker-deployment.md)，Windows 完整包与升级见 [便携版指南](docs/windows-portable.md)。发行文件以 Releases 实际附件为准；源码不包含完整权重、个人素材库或 AI 凭据。

## 当前功能

| 你要做什么 | 工作台提供什么 | 详细说明 |
| --- | --- | --- |
| 准备参考与目标 | 按身份维护素材，默认最多选择 8 张非拼图参考；检测目标人脸，逐人指派或跨图匹配 | [素材库](docs/portrait-library.md) |
| 本地换脸 | ONNX 推理、全局人物分配、遮挡/眼镜保护、手动保护画笔与边界校色；保留原始分辨率 | [本地换脸](docs/facefusion-local-swap.md) |
| 按需精修 | 确认诊断、补充意见、生成整图候选、对比并继续编辑；每轮至多生成一版，由人显式接受 | [精修工作台](docs/refinement-workbench.md)、[精修契约](docs/ai-refinement.md) |
| 保存、恢复与导出 | 自动保存工程，显式恢复继续编辑；接受后的结果用于预览和下载，ZIP 导出当前工程的最终图片 | [工程恢复](docs/studio-projects.md) |
| 回看与清理记录 | 只读查看结果；批任务、精修及旧档案支持单条/批量删除，运行中记录受保护 | [任务历史](docs/task-history.md)、[旧档案质量评价](docs/delivery-quality.md) |

换脸成功或有像素变化不代表身份、自然度已验收。处理后仍需人工检查；AI 配置和生成尺寸也有各自边界。

工作台会显示具体失败原因、实际加载的 CPU/CUDA 后端及排队状态。上传、批处理和大图证据按预算排队；无损快照、缩略图和参考图释放规则见 [内存与排队](docs/memory-scheduling.md)。中文图片路径、相机 JPG/MPO 主图及升级中断处理见 [Windows 指南](docs/windows-portable.md)。

## 本地启动

### 源码开发

准备 Python 3.11+、uv、Node.js 和 pnpm。当前开发锁面向 Linux/CUDA；CPU 容器与 Windows 便携版使用各自的运行时安装流程，不直接照搬开发锁。

在仓库根目录执行：

```bash
uv sync --dev
pnpm --dir web install
uv run python scripts/download_models.py
test -f .env || cp .env.example .env
uv run python scripts/dev.py
```

环境模板只在 `.env` 不存在时复制，已有配置保持原样。下载器校验 SHA-256；模型来源与许可见 [模型说明](models/README.md)。启动脚本只预检并运行服务，不自动安装依赖或下载权重。

| 启动后访问 | 地址 |
| --- | --- |
| Web Studio | <http://localhost:3000> |
| API 文档 | <http://localhost:8000/docs> |
| 健康检查 | `GET /health` 或 `GET /api/v1/health` |

请求字段以运行中的 OpenAPI、[通用契约](api/schemas.py)及 [精修会话契约](api/refinement/contracts.py)为准。`Ctrl+C` 停止一键启动的两个服务；预检失败时先检查模型文件和端口占用。

需要分开运行时，在两个终端分别执行：

```bash
uv run uvicorn api.app:app --host 0.0.0.0 --port 8000
pnpm --dir web dev --host 0.0.0.0 --port 3000 --strictPort
```

可用 `uv run python -c "import onnxruntime as o; print(o.get_available_providers())"` 检查执行后端；实际模型加载情况仍以健康信息为准。

### 可选 AI 配置

在本机 `.env` 为视觉和图像编辑分别配置 `AI_VISION_*` 与 `AI_IMAGE_*` 的 `MODEL`、`API_BASE`、`API_KEY`。模板中的模型只是示例，地址与密钥由使用者填写；实际凭据不进入 Git。

缺少或无效配置只禁用可选 AI：本地换脸、已存会话查看和人工接受仍可用。新 AI 轮次和模型目录会返回 `503`；健康就绪不代表上游鉴权成功。配置例子、协议前缀、错误分类及调用费用见 [模型目录](docs/model-catalog.md)。

视觉和图像编辑可用不同服务。统一入口是 LiteLLM Router，角色/别名/生成能力以 [网关 YAML](config/ai_gateway.yaml)为准，`PERSONAWEAVE_AI_CONFIG` 可指定另一份配置。更换 URL 不等于获得图像编辑能力；默认 `size: auto` 不承诺 4K，具体 endpoint 支持和重试边界也在模型目录说明。

## Docker 部署

首次配置参考 [Docker 指南](docs/docker-deployment.md)，从公开模板准备本机 `.env`，再在仓库根目录运行：

```bash
docker compose up --build -d
docker compose logs -f
```

容器同源提供工作台和 API：<http://localhost:8000> 与 <http://localhost:8000/docs>。入口会补齐并校验缺失模型；`models/`、`data/`、`outputs/` 使用持久卷。`docker compose down` 停止服务；代码更新须重新构建。

CPU/ARM64 使用：

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up --build -d
```

NVIDIA GPU 需要宿主机的 NVIDIA Container Toolkit 和 [Compose GPU 设备配置](docker-compose.yml)。CPU 依赖、端口绑定、持久目录及 Apple Silicon 约束以 Docker 指南为准；实现见 [Dockerfile](Dockerfile)和 [容器入口](docker-entrypoint.sh)。

## 素材导入与离线命令

```bash
uv run personaweave --help
uv run personaweave-import-portraits --help
```

素材导入读取来源目录的 `manifest.csv`，按内容哈希去重，损坏图片进入 `quarantine/`；托管库默认在 `data/portrait_library/`。格式见 [导入器](api/portraits/importer.py)，维护方法见 [素材库](docs/portrait-library.md)。根目录 `cli.py` 是受支持的离线源码入口。

## 开发与验证

先读 [贡献说明](CONTRIBUTING.md)和 [Agent 规则](AGENTS.md)。后端集成回归在隔离副本准备模型并使用测试 AI 配置，避免读取真实 `.env` 或写入用户数据；检查命令在该副本执行。

```bash
uv run ruff format .
uv run ruff check .
uv run pytest
pnpm --dir web format
pnpm --dir web lint
pnpm --dir web test
pnpm --dir web build
```

纯文档改动执行 [文档治理检查](docs/documentation-governance.md#机械检查)，不把历史测试成绩当作本轮验证。维护者的交付、备份与接任步骤见 [交接说明](docs/project-handover.md)和 [运行维护手册](docs/operations-runbook.md)。

## 文档导航

[文档总览](docs/README.md)按「先运行 → 使用与恢复 → 部署排错 → 开发与核验」组织专题，第一次接触项目可从那里选择路径。

| 你关心什么 | 入口 |
| --- | --- |
| 系统如何组成 | [架构概述](DESIGN.md)、[工程边界](docs/engineering-architecture.md) |
| 文档事实和代码是否一致 | [文档治理](docs/documentation-governance.md) |
| 为什么选择这些方案 | [决策笔记规则](.agents/notes/README.md) |
| 按 Wiki 专题浏览或维护发布 | [Wiki 源码首页](wiki/Home.md)、[Wiki 发布说明](docs/wiki-publication.md) |
| 查评测、夹具与资产 | [离线评测](docs/swap-benchmarks.md)、[测试素材](tests/fixtures/README.md)、[模型说明](models/README.md) |
| 查看变更时间线 | [Changelog](CHANGELOG.md)，仅作历史记录 |

实测有范围限制：[五人合影两轮精修记录](docs/refinement-live-test-20260911.md)包含真实生成和版本衔接，但候选存在相貌漂移与小遮挡错误、未定稿；流程通过不代表画质达标。最低硬件容量也没有受限环境验收承诺，参见 [内存调度](docs/memory-scheduling.md)。
