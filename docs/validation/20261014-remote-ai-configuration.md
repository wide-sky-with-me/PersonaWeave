# 2026-10-14 Mac 远端 AI 配置与尾网分流验证

## 范围与配置

实例 `mac@100.88.228.28`，原生产地址 `http://100.88.228.28:8000/`。用户确认沿用本项目已有 AI 配置，另行要求 Clash Verge 对 Tailscale 分流并返回真实 IP。仅迁移 AI 六项配置；原 Mac `.env` 及私有 Clash 配置在 Mac 本地备份，权限 0600，凭据不进入报告、仓库或镜像。未修改本机开发 `.env`。

视觉模型为 `openai/gpt-6-astra`，图像编辑模型为 `openai/gpt-image-2.5-flare`；底层 ID 来自用户已有配置，协议前缀修正了视觉裸名的供应商识别失败。应用仍使用 [原生模型目录](../../config/ai_gateway.yaml) 的两个业务别名，不新增供应商私有调用路径。来源与语义见 [模型配置](../model-catalog.md)。

## AI 非付费检查

- 六项导入后，以临时 HMAC 比较容器值与选定配置：全部一致，工具只输出比较结论。
- 环境校验和实际 LiteLLM Router 初始化通过；`/health` 的 pipeline_ready、ai_refinement_ready 均为 true，六个本地模型仍为 CPU Provider。
- 原地址 `/api/v1/ai/models` 返回 200，公开目录包含默认图像模型。
- 初次容器出站失败，解析到 `198.18.0.6` / `2001:2::6`；复用 Mac 已有代理验证两路 `/models` 均返回 200，配置模型均在目录中。运行代理已持久保存，localhost 与服务尾网地址设为 NO_PROXY，见 [运行代理决策](../../.agents/notes/platform/20261014-use-host-proxy-for-container-ai.md)。
- Chromium 页面精修勾选入口启用、默认未选中，没有页面异常或失败响应。

模型目录鉴权与就绪不代表已验收 `/images/edits` 的完整生成能力、实际费用或结果画质。本次没有付费生成或视觉诊断调用，也没有创建精修会话和候选。

## Clash Verge 与国内网络实测

核心为 Mihomo v1.19.32；当前订阅原规则 566 条。全局和订阅扩展均为默认空脚本，用户 DNS 设置页未启用覆盖，生效 DNS 继承订阅 fake-ip。修改 [全局扩展](../../scripts/macos-clash-tailnet.js)，保留全部节点、代理组、其他配置和原规则；前置 7 条 Tailscale 进程/域名/网段 DIRECT，TUN 排除尾网段，DNS 黑名单加入尾网域名及当前 AI 上游，MagicDNS 指向 Quad100。取舍见 [尾网与真实 DNS 决策](../../.agents/notes/platform/20261014-tailnet-real-dns-clash.md)。

持久源在用户的 Clash Verge 配置目录 `profiles/Script.js`；按同一函数更新生成配置，经服务 Unix socket 热加载返回 204。备份位于 Mac 部署目录 `network-backups/20261014-100428/`；临时回滚看守在新 SSH、DNS 和健康检查通过后已取消。没有修改私人订阅来源、账户或节点。

实际结果：

- Core API 的前 7 条规则为 DIRECT，余下 566 条订阅规则保留。
- 控制平面从假 IP 改为 `192.200.0.*` 的真实地址；AI 上游为 `110.42.10.198`。容器实际 socket 解析也得到真实地址，不仅是宿主 dig。
- 容器关闭环境代理后，两路上游模型目录直接鉴权均为 200，配置模型存在；运行代理作为已验证路径继续保留。
- Mac 控制平面直连 200，登录页面直连 302；Tailscale 在线，到当前操作端连续两组 ping 均为真正 UDP 点对点直连，延迟 4–6 ms。结果只适用于当次对端及网络，不证明所有国内线路或所有尾网节点均能直连；Clash DIRECT 与 UDP 打洞也不是同一结论。
- 原生产地址及宿主启动守卫继续正常，AI 目录就绪；没有再次整机重启，不把热加载当成整机冷启动验收。

## 证据与维护

管理机证据位于 `outputs/deployment/20261014-host-reboot/`：`ai-network-verified.json`、`ai-ready-browser.json`、`clash-tailnet-verified.json`、最终网页截图与对应检查 JSON。正文不保存私人代理配置、API 凭据或完整上游模型清单。

[扩展测试](../../tests/test_macos_clash_tailnet.py) 验证保留订阅、两类网段、真实 DNS 例外、重复应用幂等及遇到不同过滤模式时不修改输入。实例网络如变化，先检查真实 DNS 与控制/DERP 可达性；必要时把对应域名代理例外置于进程 DIRECT 前，继续保留尾网段排除。操作入口见 [Docker 部署指南](../docker-deployment.md)，本次宿主重启故障独立记录于 [启动转发验收](20261014-host-reboot.md)。
