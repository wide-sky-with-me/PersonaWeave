# 2026-10-14 Mac 网页识别性能排障

## 范围与输入

用户报告面部识别极慢。测量对象是 `http://100.88.228.28:8000` 的 CPU Docker 实例，不将结果外推为独立 Windows 便携实例的性能。模型识别在 Mac 容器执行，网页使用同源 API；调用方见 [api.ts](../../web/src/services/api.ts) 与 [SourceGallery](../../web/src/components/studio/SourceGallery.tsx)。

同图对照使用 [source-01.png](../../tests/fixtures/source-01.png)，远端副本为 `data/test_target/01.png`。两侧 SHA-256 均为 `4160852cddeb35463498f05b4c0ae167643bbb8f35529aa43ac501147fecbac1`，每次检测均返回一张脸。原图 base64 JSON 请求为 1248779 字节，托管路径 JSON 为 48 字节。检测入口见 [faces.py](../../api/routes/faces.py)。

## 实测结果

测量日期为 2026-10-14，时区 Asia/Shanghai。容器内通过 localhost 调用现有 HTTP API，外部通过同一 Tailscale 地址调用；不是重新创建 ONNX session 的离线基准。

| 路径 | 时间（秒） | 说明 |
| --- | ---: | --- |
| 容器内引用同图检测 | 0.101 | 已初始化流水线，本地 HTTP 与完整检测合计 |
| 外部引用已存同图 | 1.178 | 48 字节请求，无原图上传 |
| 外部上传同图再检测 | 18.937 | 停止素材传输后的基线，单次 |
| 域名分流后的上传检测 | 8.561 | 单次 |
| 保留分流、撤去 UDP 试验后的重复上传 | 9.792 / 5.940 / 6.237 | 连续三次，未压缩图片或改变模型 |

另用 `data/test_images/group-01.png` 在容器内上传检测，1067×1600 图像返回五张脸，合计 0.134 秒。健康接口显示所有模型为 CPU Provider，推理队列空闲。首次资源采样为 1.27 GiB / 7.738 GiB，空闲 CPU 0.33%。这些样本证明当次主要延迟在上传/传输，不证明所有图片或跨图身份聚类均有同一耗时。

## 操作与限制

- 停止六路素材同步作业，保留隐藏暂存目录；素材库未启用，状态见 [素材库记录](20261014-macos-portrait-library.md)。
- Mihomo 连接证据显示 Tailscale 控制/日志域名经美国代理，进程名字段为空。客户端全局脚本增加 `DOMAIN-SUFFIX,tailscale.com,DIRECT`，生效配置只增加这一条规则；保留 Mac 私网接口分流、TUN、规则模式及全部订阅内容。
- 热加载后仅重连匹配 Tailscale 域名的旧连接，观察到 `DomainSuffix` / `DIRECT` 命中。试验过 Mac 单一公网 UDP 端点及 Tailscale 独占源端口的直连规则，均未建立点对点连接；两项 UDP 规则已移除。
- 链路仍为 DERP(lax)。重复上传耗时存在波动，改善是本次样本结果，不宣称稳定倍数或网络问题全部解决。分流诊断与可回滚配置副本在管理机的 `outputs/deployment/` 和 Clash 的本地备份目录，文档不复制配置中的凭据。
- 未改变模型 Provider、共享推理并发或图像精度，未部署应用代码、创建测试工程、调用付费 AI；长期取舍见 [排障决策](../../.agents/notes/platform/20261014-separate-inference-from-tailscale-upload.md)。

## 复现与检查

在容器内请求 `POST /api/v1/detect-faces`，JSON 使用 `image_url=/api/v1/data/test_target/01.png`，以单调时钟统计完整响应。外部对同一入口分别发送该托管路径和夹具原始字节的 PNG data URI；保存请求字节数、耗时、脸数，并先核对文件摘要。不要将私网完整 HTTP URL当作托管路径：受控加载规则只接受托管相对路径或回环 URL，见 [image_security.py](../../core/image_security.py)。

分流脚本通过语法、重复应用幂等性与既有规则/代理保留断言；结构比较确认运行配置仅增加 Tailscale 域名规则。隔离 Chromium 上的生产页面完成渲染，“素材库选入”按钮可见，模型流水线就绪且推理队列空闲；截图和 JSON 证据位于管理机的诊断输出目录。项目环境首次浏览器检查因缺少 Playwright 失败，改用 `uv run --no-project --with playwright` 的隔离依赖后通过，项目依赖未改动。尚未证明 Windows 独立应用性能、点对点连通或素材迁移完成。

文档检查首次因新决策笔记缺少当前文档的直接链接失败，补齐 Docker 部署指南的链接后，`uv run pytest tests/test_documentation_consistency.py tests/test_documentation_governance.py` 的 23 项检查全部通过；`git diff --check` 通过。应用代码未改动，未将此次文档检查表述为完整产品回归。
