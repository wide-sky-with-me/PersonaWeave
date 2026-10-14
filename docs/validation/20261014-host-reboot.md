# 2026-10-14 Mac 宿主重启后端口转发故障

## 实例与复现

目标 `mac@100.88.228.28`，服务 `http://100.88.228.28:8000/`。Mac 启动时间为 08:47:47；09:21 排查时 Colima VZ、Homebrew service 及容器均运行，容器为 healthy，实际六模型 CPU Provider 正常。原 R3 镜像摘要保持 `sha256:86c6482e516e0c74706c9552f4f11595f3277ce3c604c6237853101dcfa54078`，模型和持久目录不变。

故障证据：

- 管理机与 Mac 请求原地址均连接失败；Mac 没有 8000 TCP 监听。
- VM 的 loopback 包含 `100.88.228.28`，且有对应 8000 监听；VM 内和容器内 `/health` 正常。
- 08:49:46 的 hostagent 日志两次记录 `Not forwarding TCP 100.88.228.28:8000`。
- 当次生成的 Lima 规则包含 LAN 地址，却没有尾网地址的 guestIP/hostIP 规则。

这次不是容器未启动。Colima 启动快照漏掉尚未就绪的 Tailscale 地址；VM 后续复制地址没有补建宿主转发。既有容器、VM 重启验收没有覆盖这个整机登录启动顺序。

## 处置与实际验证

地址就绪后执行 `brew services restart colima`，原地址恢复，Mac 出现仅绑定尾网地址的 8000 监听。安装 [启动守卫](../../scripts/macos-colima-boot-guard.sh) 及用户 LaunchAgent `io.personaweave.boot-guard`；保留原 `sh.brew.colima` service，不改应用容器或发布边界。维护语义见 [部署指南](../docker-deployment.md)，原因及替代方案见 [决策](../../.agents/notes/platform/20261014-wait-for-tailnet-colima-forwarding.md)。

验证分层：

- `sh -n`、`uv run ruff format .`、`uv run ruff check .` 通过；[模拟故障回归](../../tests/test_macos_colima_boot_guard.py) 12 项通过，覆盖地址晚到、VM 未就绪、完整/缺失规则、其他容器、推理忙、冷却和重启失败。
- plist 校验通过，加载到 `gui/501`；实际周期执行返回 0。健康配置手工重复执行，容器 StartedAt 不变。
- 回放真实重启前的缺失规则快照作为守卫检查输入，真实执行 Homebrew 服务重启；不改当前 Lima 配置。恢复后原地址健康、六个模型就绪、8000 监听正确，镜像和重启策略不变；周期守卫累计执行仍退出 0。回放快照已从目标机删除。
- Chromium 原地址返回 200，打开人物库并等索引与图片全部完成；64 身份、1024 参考、64 可见人物缩略图加载成功，无页面异常和失败响应。

证据位于管理机 `outputs/deployment/20261014-host-reboot/`：`ha-before.stderr.log`、`lima-before.yaml`、`colima-before.yaml`、`guard-real-recovery.json`、修复回放后的 `guard-browser-final.json` 及截图。日志与配置属于非版本控制运行证据，不包含业务凭据；不把浏览器索引未完成的中间截图当作成功验收。启动守卫和文档检查合计 35 项通过，`git diff --check` 干净。

## 运维与边界

目标机的守卫为 `/Users/mac/services/personaweave/colima-boot-guard.sh`，LaunchAgent 为该用户 `Library/LaunchAgents/io.personaweave.boot-guard.plist`，日志为部署目录 `boot-guard.log`。检查 `launchctl print gui/501/io.personaweave.boot-guard`；手工执行可使用 `PERSONAWEAVE_BIND_ADDRESS=100.88.228.28 /bin/sh ./colima-boot-guard.sh`。移除守卫先 bootout 此 label，再移除这两个专属文件；原 Homebrew Colima service 不受移除影响。

本次使用用户实际晨间重启的故障证据，并验证启动守卫的真实修复路径；未再次重启整个 Mac，未验证无人登录、FileVault 解锁前的冷启动。守卫运行于登录用户域，不保证这些场景。数据、默认人物库、模型及回滚镜像均保留；没有付费 AI 请求。
