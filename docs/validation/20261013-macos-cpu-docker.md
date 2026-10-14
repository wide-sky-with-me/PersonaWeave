# 2026-10-13 Mac ARM64 Docker 部署验收

## 范围与实例

本记录验证 PersonaWeave 工作树的 CPU 容器部署，不代表完整画质评测或全部 UI 操作验收。构建配置见 [部署指南](../docker-deployment.md)、[Dockerfile](../../Dockerfile)、[CPU Compose](../../docker-compose.cpu.yml)；依赖隔离依据见 [CPU Runtime 决策](../../.agents/notes/platform/20261013-pin-cpu-runtime-and-ignore-dev-overrides.md)。

- 目标：`mac@100.88.228.28`；访问入口：`http://100.88.228.28:8000/`，访问机器须能到达该 Tailscale 地址。
- 部署目录：`/Users/mac/services/personaweave`。通过已有 SSH 身份连接；未迁移个人凭据、素材库或历史数据库。
- 机器：Apple M4、macOS 27.0.1；Colima VZ 使用 ARM64、4 CPU、8 GiB 内存、40 GiB 数据盘。这是实例配置，不是最低硬件需求。
- Docker server 29.5.2；镜像 `personaweave:cpu` 为 `linux/arm64`，运行 Python 3.11.17、CPU ONNX Runtime 1.23.2。
- 实际镜像 ID：`sha256:dfcf89f0f1224ca85ca9951a30c741ae27f26601a30a5c2bbebde517f61623d1`。
- 仅发布 `100.88.228.28:8000`；`models`、`data`、`outputs` 从部署目录读写挂载到容器对应的 `/app/` 子目录。

## 输入与实测结果

必需模型按 [下载器的摘要来源](../../scripts/download_models.py) 下载并校验，六个生产模型全部通过，合计 914549088 字节。仅使用版本控制中的 `tests/fixtures/source-01.png`、`source-02.png`、`group-01.png`、`group-02.png` 预置样例。

真实请求调用本地换脸，AI 网关未配置，未发起付费 AI 请求：

- `/health` 返回 `status=ok`、`pipeline_ready=true`，六个实际模型的 Provider 均为 `CPUExecutionProvider`；`gpu_available=false`、`ai_refinement_ready=false`。
- 容器重启验收：检测到 5 张脸，替换 1 张；单次请求 2.08 秒。该数值仅代表此样例，不作为吞吐或性能承诺。
- 产物为 1067×1600 JPEG，874112 字节；SHA-256：`864651ad9dc6d22995e0ede7a4581b4c27d6480f9982535ba132f667ecb268ce`。
- 临时工程保存后重启容器，工程文档保持一致，产物下载字节与摘要完全一致；仅删除本次创建的工程。
- VM 级验收：停止 Colima，再由 Homebrew service 启动；Docker 自动恢复容器，同一临时工程和产物保持一致。该次换脸请求为 1.90 秒，产物摘要与上次一致。
- Homebrew 返回 `Running: true`、`Loaded: true`；Docker 重启策略为 `unless-stopped`。后续复核时服务已连续运行约三小时，容器仍为 `healthy`。
- 真实 Chrome 直连部署地址返回 HTTP 200；桌面 1440×1000、手机 390×844 初始界面均加载，页面异常、控制台错误和失败请求均为空。

当次 JSON、诊断脚本、截图和 JPEG 位于管理机 `outputs/deployment/20261013-macos/`，属于非版本控制运行产物；服务端保留对应 API/VM 验收报告。正式证据摘要以本记录为准。

## 复现与维护

下列命令在目标 Mac 的部署目录执行；验收脚本接收一个 argv 命令，不直接解释 shell 操作符：

```sh
export PATH=/opt/homebrew/bin:/opt/homebrew/sbin:$PATH
cd "$HOME/services/personaweave"
./start.sh
docker --context colima compose -f docker-compose.yml -f docker-compose.cpu.yml ps
curl --noproxy '*' -fsS http://100.88.228.28:8000/health
uv --no-config run --no-project --python /usr/bin/python3 smoke.py \
  --url http://100.88.228.28:8000 \
  --restart-command 'docker --context colima compose -f docker-compose.yml -f docker-compose.cpu.yml restart personaweave' \
  --report outputs/deployment/20261013-macos/smoke-report.json
```

`start.sh` 只使用已构建镜像启动，更新构建步骤见部署指南。VM 级复测把 restart-command 换成 `sh ./restart-vm.sh`；该脚本只操作此部署使用的 Colima VM。日志命令为同一 Compose 调用的 `logs --tail=100 personaweave`。数据库和输出备份保留原挂载目录；替换容器不删除这些目录。

## 排障、检查与边界

DNS 故障来自新 VM 中悬空的 resolver 链接；保留原 Colima 配置和链接备份，并为此 VM 配置有效 DNS 和幂等启动修复。镜像仓库与构建阶段使用目标机已有代理。Node 构建显式开启环境代理后，Corepack、锁定依赖安装及生产前端构建通过；未关闭 TLS 校验。浏览器默认代理导航失败，显式直连后成功，未修改系统浏览器设置。

Ruff 格式与检查、CPU 构建回归和文档检查通过（30 项）；前端静态检查通过；远端执行 `pnpm build`，完成 TypeScript 与 Vite 生产构建。模型、CPU 会话、真实请求及两级重启为本次实际 ARM64 验收，区别于既有开发机测试。

CPU 容器不使用 Apple Metal/CoreML GPU。AI 精修未配置；模型费用表的公共网络读取失败会回退本地备份，本地换脸保持可用。登录后的 Colima 启动已配置并验证进程运行；没有执行整机重启，也未验证无人登录的冷启动。未提交或推送代码变更。
