# CPU 容器独立固定 runtime，并隔离开发 uv 覆盖规则

状态：有效
日期：2026-10-13

## 背景

[初始 CPU 构建决策](20261013-arm64-cpu-containers.md) 的同版本替换在实测中失效。开发锁的 GPU 1.20.2 有包，但 [PyPI CPU 1.20.2](https://pypi.org/pypi/onnxruntime/1.20.2/json) 返回 404；[CPU 1.23.2](https://pypi.org/project/onnxruntime/1.23.2/) 有 Linux ARM64 / CPython 3.11 wheel。另外，`pyproject.toml` 的 uv override 把 onnxruntime 标为 `sys_platform == 'never'`，普通 uv pip dry-run 会静默排除明确写入 requirements 的 CPU 包。

## 决策

- 独立 `cpu` 目标、默认 CUDA 目标、非 CUDA 锁定版本和 Colima 部署架构沿用初始决策；CPU 镜像以构建脚本的一个常量固定 `onnxruntime==1.23.2`。
- 安装 CPU requirements 和项目时使用 `uv --no-config pip install --no-deps`。uv export 仍读取原开发配置和冻结锁，安装阶段隔离它，避免恢复 CUDA 或排除 CPU runtime。
- 构建保留真实导入及 CPU Provider 断言；平台可用性必须通过完整 ARM64 解析、实机镜像构建和真实模型推理核验。
- CPU runtime 独立于 Windows 构建常量，两者虽然此刻版本相同，但验证平台和升级条件不同，不跨平台借用验证结论。

## 被放弃的方案

- **始终按 GPU 版本号推导 CPU 包**：CPU 和 GPU 发布序列不完全相同，存在 GPU 有而 CPU 缺失的版本。
- **以普通 uv pip dry-run 成功作为完整验证**：项目 override 会消掉 runtime；必须隔离配置，并确认解析输出确实包含 CPU onnxruntime。
- **直接删除开发 override 或更新 GPU 锁**：这是既有 CUDA 开发环境避免两套 ORT 文件互相覆盖的规则，本次只改变 CPU 镜像。
- **动态选最新 CPU 包**：引入隐式升级和不可重复构建，改为明确 pin 并逐平台验收。

## 影响与验证

实现与 pin 位于 [依赖转换](../../../scripts/container_requirements.py)，隔离规则位于 [Dockerfile](../../../Dockerfile)，契约见 [容器依赖回归](../../../tests/test_container_requirements.py)，操作见 [Docker 部署](../../../docs/docker-deployment.md)。使用 `uv --no-config pip install --dry-run --no-deps --python-version 3.11 --python-platform aarch64-manylinux_2_28` 核验 CPU 包可用；实际 ONNX 推理和持久化重启结果写入独立验收记录，不从解析成功外推画质或付费 AI 可用。
