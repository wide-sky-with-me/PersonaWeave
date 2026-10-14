# Mac 同网段双入口验收

日期：2026-10-14；镜像沿用 `personaweave:cpu-20261014-production-r3`，持久模型、素材、工程及 AI 配置保留。证据目录：`outputs/deployment/20261014-lan/`。

## 网络与发布

客户端路由：`192.168.100.84 dev eth3 src 192.168.100.120`。Mac WiFi 硬件接口为 en1，`ipconfig getifaddr en1` 返回 `192.168.100.84`；不以 networksetup 的 SSID 输出判断 HTTP 连通性。

修改前 LAN 8000 连接失败；Docker 仅发布 `100.88.228.28:8000`。核对 Lima 含两地址转发、应用推理空闲后，以现有 CPU 镜像追加 [LAN 覆盖](../../docker-compose.lan.yml)，远端环境保存 LAN 地址与三个 Compose 文件。仅新增显式 LAN 发布，不使用全接口监听。普通 `docker compose config` 与 inspect 均确认双绑定、原镜像摘要、三个原持久挂载及 unless-stopped；两个入口 health 均成功。环境备份留在 Mac，权限 0600，不进入仓库。

可访问入口：`http://192.168.100.84:8000` 与 `http://100.88.228.28:8000`。局域网连接使用实际 LAN 路由，客户端无需 Tailscale 转发。服务端的启动条件、修改与撤回方式见 [部署指南](../docker-deployment.md)。

## 真实网页流程

初轮浏览器继承系统代理，TCP 对端是回环代理端口；该轮功能通过但不作为无代理 LAN 证明。正式复测以 Chromium `--no-proxy-server` 启动，页面及 swap-sync 响应的实际 TCP 对端均为 `192.168.100.84:8000`。

在新浏览器通过网页上传仓库 `group-01.png`，创建“局域网直连验收”人物并上传 `source-01.png`，在人脸 #1 快速指派该来源，执行单图换脸。检测五脸、换入一脸；点击至 HTTP 结果为 3.642 秒，服务端 cost_ms=1847.57，分别记录传输/UI 与服务端处理，不推定所有照片的耗时。工程保存后刷新，来源人物及完整画布恢复，网页无异常和失败 HTTP 响应。见 `lan-direct-ui-acceptance.json` 与 `lan-direct-success.png`。

结果通过无代理 HTTP 下载，完整解码为 JPEG、1067×1600、874112 字节；SHA-256 为 `864651ad9dc6d22995e0ede7a4581b4c27d6480f9982535ba132f667ecb268ce`，文件 `lan-direct-result.jpg`。与上传原图目视比较，左侧指定人脸有变化，未见整幅错位；不将单样本目视与诊断当作普遍身份或自然度保证。

人物库弹窗通过 LAN 地址载入 64 身份、1024 参考，64 张列表肖像全部完成解码，见 `lan-library.json`。该弹窗记录来自初轮带系统代理浏览器，证明该入口资源完整；完整上传换脸与恢复的直达网络证据来自正式无代理复测。AI checkbox 可用且未勾选，健康显示就绪；测试额外拦截精修 POST，观察到零次请求，没有进行付费生成。

## 排障、清理与限制

自动化初次将“新建人物”的上传入口误定位到背景侧栏，产生定位超时；按真实创建弹窗修正定位后通过，没有修改产品或放宽业务断言。测试工程按创建响应及对应上传日志精确定位，核对仓库样本摘要后按最新 revision 删除；只清理本次两条 sync 产物，保留本地证据、用户数据和默认素材。清理后的测试工程/产物 URL 缺失是预期，见 `cleanup.json`。

静态配置回归及文档检查命令：`uv run ruff format .`、`uv run ruff check .`、`uv run pytest tests/test_container_requirements.py tests/test_macos_colima_boot_guard.py tests/test_macos_clash_tailnet.py tests/test_documentation_consistency.py tests/test_documentation_governance.py`。format/lint 干净，49 项回归通过，`git diff --check` 通过。最终无代理双入口 health、发布地址、原镜像摘要及默认 Compose 合并证据见 `network-publication.json`。配置决策见 [双入口笔记](../../.agents/notes/platform/20261014-explicit-lan-and-tailnet-bindings.md)。

本次没有再次重启 Mac，没有验证 AI 实际生成；IP 为当前 DHCP 地址，换 WiFi/租约变更须更新配置及 VM 转发。同一 WiFi 本身不保证所有设备均可通信：本次证明该客户端直达，其他访客网络或 AP 隔离状态另行实测。
