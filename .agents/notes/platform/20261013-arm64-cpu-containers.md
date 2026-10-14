# ARM64 使用独立 CPU 镜像，开发 CUDA 锁文件保持不变

状态：被 20261013-pin-cpu-runtime-and-ignore-dev-overrides.md 取代
日期：2026-10-13

## 背景

用户要求把 PersonaWeave 部署到 Apple M4 Mac。实机为 Darwin ARM64，现有 Dockerfile 直接同步包含 NVIDIA/CUDA 与 onnxruntime-gpu 的开发依赖。普通 CPU ONNX 容器不使用 Mac 的 Metal 后端，照搬 CUDA 依赖会造成平台不匹配并携带无用的大包。

## 决策

- 在同一 Dockerfile 增加 `cpu` 目标；默认最后一个 `backend` 目标及开发锁文件保持原配置。
- CPU 依赖从 uv.lock 导出，保留全部非 CUDA 版本及 marker；用与锁定 GPU 包相同版本的 CPU ONNX Runtime 替代，不另外维护 runtime 版本源。只装 headless OpenCV。
- 项目安装使用 uv 的 `--no-deps`；容器直接用虚拟环境中的 uvicorn，不通过 uv run 再同步 CUDA 元数据。
- 远端采用 Colima 的 VZ ARM64 VM 和 Docker；模型、运行数据和输出留在持久目录，部署不复制本机个人配置及历史。
- 端口支持明确绑定到所需私网接口；保留默认绑定行为以兼容现有部署。

## 被放弃的方案

- **在 M4 上模拟 x86 CUDA 镜像**：多余的 CUDA 依赖没有对应硬件，且仿真增加推理开销。
- **把开发依赖全面改成 CPU/可选 CUDA**：本次仅部署，修改整个开发锁和已有 GPU 路径会扩大回归范围。
- **复用 Windows 便携版 CPU runtime 常量**：Windows 构建有独立的已验版本，不应成为 Linux ARM64 的第二份版本源。
- **无容器的 macOS Python 常驻服务**：与用户优先 Docker 的要求不同，也将依赖和生命周期暴露到宿主环境。

## 影响与验证

实现为 [Dockerfile](../../../Dockerfile)、[CPU Compose](../../../docker-compose.cpu.yml) 与 [锁定依赖转换](../../../scripts/container_requirements.py)。契约由 [回归](../../../tests/test_container_requirements.py) 断言，操作和限制见 [Docker 部署](../../../docs/docker-deployment.md)。实际平台支持需以目标 ARM64 镜像构建、主要模块导入、真实本地推理与持久化重启验收证明，不能只用静态导出或 CPU Provider 名称宣称推理可用。冷启动仍依赖 Mac 登录后的 Colima 服务；本地流程成功不代表付费 AI 或成片质量验收。
