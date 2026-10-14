# 为尾网和容器上游保留直连与真实 DNS
状态：有效
日期：2026-10-14

## 背景

用户要求调整 Mac 的 Clash Verge：Tailscale 分流和相关域名返回真实 IP，并询问国内网络能否直连。当前订阅使用 fake-ip，控制平面与 AI 上游分别解析到 `198.18.0.8` 和 `198.18.0.6`，容器绕开宿主 TUN 出站时连接失败。唯一当前订阅的全局、订阅扩展脚本均为默认空脚本，DNS 设置页没有启用覆盖。

## 决策

在已备份的全局扩展加入 Tailscale 实际 GUI/网络扩展进程、服务域名及 IPv4/IPv6 尾网优先 DIRECT；TUN 排除尾网段，尾网与现有 AI 上游加入 fake-ip 黑名单，MagicDNS 查询交给 100.100.100.100。其余订阅规则、节点、代理组、DNS 上游和 fake-ip 模式保留。

持久源为全局 Script.js；同时按相同函数生成运行配置并通过服务 Unix socket 热加载，避免仅改临时运行文件。源脚本遇到不同过滤模式时显式停止，不擅自解释 whitelist/rule 语义。修改带本机私有备份与短时回滚看守，新 SSH、DNS 和应用健康通过后取消看守。

国内网络按当前实例实测，而不是固定标签决定策略：该 Mac 控制平面直连 200，登录入口 302；到当前操作端三个 ping 均为真正点对点直连。若控制/DERP 在其他线路不可达，可把对应域名代理规则放在进程 DIRECT 之前；尾网段依然绕开代理。Clash DIRECT 与 Tailscale 的 UDP 打洞不是同一概念，不承诺所有线路或重启后始终使用同一路径。

## 被放弃的方案

- 全局关闭 fake-ip：超出所需范围，会改变其他代理应用的解析方式。
- 只设进程直连：没有修正 DNS，也不能避免尾网虚拟接口被另一 TUN 抢占。
- 固定上游或控制平面 IP 到 hosts：丢失正常 DNS 更新与多地址容错。
- 只改生成的 clash-verge.yaml：下一次订阅重建会覆盖，需修改全局扩展源。
- 一律代理或一律认定国内不通：与该实例的实测不符。

## 影响与验证

局部补充 [运行代理决策](20261014-use-host-proxy-for-container-ai.md)：解析修正后容器也可直接鉴权；已验证的运行代理仍保留。私人订阅及连接凭据只在 Mac 本地备份，不进入仓库。没有付费生成请求，未再次整机重启。

实现：[全局扩展](../../../scripts/macos-clash-tailnet.js)；回归：[不变量测试](../../../tests/test_macos_clash_tailnet.py)；实例证据与边界：[远端 AI 与网络](../../../docs/validation/20261014-remote-ai-configuration.md)。语义依据：[Mihomo DNS](https://wiki.metacubex.one/config/dns/)、[Clash Verge 扩展](https://www.clashverge.dev/guide/script.html)、[Tailscale 连接类型](https://tailscale.com/docs/reference/connection-types)。
