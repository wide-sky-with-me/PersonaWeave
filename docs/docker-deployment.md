# Docker 部署

同一容器提供生产前端和 API，默认端口 8000，不使用 Vite 开发服务器。构建与启动来源：[Dockerfile](../Dockerfile)、[Compose](../docker-compose.yml)、[容器入口](../docker-entrypoint.sh)。

## 镜像选择

**前提。** 默认 `backend` 构建目标使用含 Linux/CUDA 依赖的开发锁文件。纯 CPU 环境选择 `cpu` 目标和 [CPU Compose 覆盖](../docker-compose.cpu.yml)，补充主配置。

**操作。** 在项目根目录执行，保留已有 `.env`：

```bash
# 在项目根目录执行；已有 .env 时保留它。
test -e .env || cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up --build -d
```

**构建边界。** [container_requirements.py](../scripts/container_requirements.py) 从 `uv export` 保留非 CUDA 依赖版本与环境标记，排除 NVIDIA 依赖，只保留 headless OpenCV，固定使用提供 Linux ARM64 wheel 的 `onnxruntime==1.23.2`。它不改开发环境或 `uv.lock`。

安装使用 `uv --no-config pip install --no-deps`：隔离开发配置中排除 CPU runtime 的覆盖规则，也避免项目元数据带回 CUDA 依赖。导出发现未锁定依赖、GPU runtime 缺失/冲突或缺少 headless OpenCV 时停止；构建验证主要模块导入及 CPU Provider（ONNX Runtime 的执行后端）。回归：[容器依赖测试](../tests/test_container_requirements.py)；依据：[CPU runtime 决策](../.agents/notes/platform/20261013-pin-cpu-runtime-and-ignore-dev-overrides.md)。

## 数据与模型

**持久数据。** Compose 将项目的 `models/`、`data/`、`outputs/` 挂载到容器对应目录，重建不覆盖数据。备份或移动安装前停止写入，保留这三个目录和服务配置。个人 `.env`、数据库、照片和结果不进入镜像或公共分发包，由 `.dockerignore` 排除。

**素材与模型。** 人物素材库须独立安装完整快照或按 manifest（导入清单）导入，空挂载目录不预置人物。要求见 [素材库维护](portrait-library.md#新实例与迁移)。首次启动缺少必需模型时，入口运行带摘要校验的下载器；离线部署先按 [模型说明](../models/README.md) 验证并复制缓存，文件存在不等于完整。

**样例。** [版本化夹具](../tests/fixtures/README.md) 不在容器镜像内。需要示例按钮时，把四张夹具按 Windows 构建脚本的 `SAMPLES` 映射放入 `data/test_target/`、`data/test_images/`。

**AI 配置。** 远端实例使用自己的 `.env`，不自动复制另一台机器的凭据。按用户明确选定的来源迁移时，只私下导入所需六项并保留原文件；字段与协议前缀见 [模型配置](model-catalog.md)。缺配置时本地换脸和已存会话仍可用，AI 就绪为 false；健康检查不验证付费上游。Mac 实例的配置、只读鉴权与网页证据见 [AI 与网络验收](validation/20261014-remote-ai-configuration.md)。

## 端口与访问

**单入口。** `PERSONAWEAVE_BIND_ADDRESS`、`PERSONAWEAVE_PORT` 控制宿主发布地址/端口，默认全接口 8000；容器内固定 8000，同源前端访问同一端口的 API。内部使用绑定实际私网接口，仅本机使用可设 `127.0.0.1`。服务没有登录隔离，不应直接暴露公网。

**双入口操作。** 同时保留尾网（Tailscale 私网）和 LAN（局域网）时，保留原绑定地址，在实例 `.env` 填实际 `PERSONAWEAVE_LAN_ADDRESS`，追加 [LAN 覆盖](../docker-compose.lan.yml)：

`docker compose -f docker-compose.yml -f docker-compose.cpu.yml -f docker-compose.lan.yml up --no-build -d`

`--no-build` 仅用于已有 CPU 镜像。该覆盖只追加指定地址，不改镜像、数据或原入口。设置 `COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml:docker-compose.lan.yml` 后，普通 Compose 调用会保留全部覆盖；显式 `-f` 调用仍须包含 LAN 文件。

**排错。** DHCP 地址变化后更新变量，核对新地址已进入 Colima 转发规则，再重建容器。固定路由器 DHCP 租约可减少变动；客户端隔离与防火墙仍须实际连接验证。依据：[双入口决策](../.agents/notes/platform/20261014-explicit-lan-and-tailnet-bindings.md)；实例入口：[LAN 验收](validation/20261014-lan-access.md)。

## Apple Silicon / Colima

**适用边界。** macOS 通过 Linux VM（虚拟机）运行 ARM64 容器。普通 CPU ONNX 容器不使用 Apple Metal/CoreML GPU；Docker Model Runner 的 Metal 支持属于其专属引擎，不是本项目的 ONNX 后端，见 [Docker GPU 边界](https://docs.docker.com/desktop/features/gpu/)。

**环境前提。** Homebrew 可安装 `colima`、`docker`、`docker-compose`、`docker-buildx`；Docker CLI 须发现 Compose/Buildx 插件。Colima 使用 VZ 与 ARM64 架构；CPU、内存、磁盘按机器容量分配，实例分配量不是已验证的最低配置。

**构建代理。** 镜像仓库连接由 Docker daemon 配置，构建步骤使用 `HTTP_PROXY` / `HTTPS_PROXY` build args，见 [Docker daemon 代理](https://docs.docker.com/engine/daemon/proxy/)。前端开启 `NODE_USE_ENV_PROXY=1`，要求 Node 22.21+，使 Corepack 与 Node fetch 显式走代理，见 [Node 环境代理](https://nodejs.org/api/http.html#built-in-proxy-support)。这些配置仅影响构建，不把代理地址/凭据固化到运行环境；DNS 故障须核对 VM 的 `/etc/resolv.conf`，只查宿主 DNS 不够。

**运行代理。** 出站代理另在实例 `.env` 配置，`NO_PROXY` 保留回环与应用尾网地址。Mac 的 Clash Verge DNS 假 IP 可能导致容器健康却连不上上游。该实例的 [全局扩展](../scripts/macos-clash-tailnet.js) 让尾网和所配 AI 域名返回真实 IP，前置尾网 DIRECT，并从 TUN（虚拟网络接口）排除其网段，其他代理设置保留。依据：[网络决策](../.agents/notes/platform/20261014-tailnet-real-dns-clash.md)。DIRECT 不证明每个对端都完成 UDP 打洞；国内控制/DERP（中继）可达性须实测，必要的域名代理例外放在进程直连之前。

**地址与挂载。** [Colima 配置](https://colima.run/docs/configuration/) 的 `network.hostAddresses` 把 Mac 实际接口地址复制到 VM，供指定地址发布；挂载目录放在该用户家目录下，默认共享边界见 [FAQ](https://colima.run/docs/faq/)。访问须从另一台机器验证，容器内 localhost 检查不能替代。

**登录启动。** `brew services start colima` 配置登录后启动，不保证无人登录的冷启动可用。指定私网地址的转发规则要求地址在 VM 启动时存在；`hostAddresses: true` 和健康容器不证明宿主发布成功，须核对生成规则及 Mac 实际端口监听。

Mac 实例的 [启动守卫](../scripts/macos-colima-boot-guard.sh) 每 30 秒确认地址与 VM 就绪，缺转发规则时重启原 Homebrew service；有其他运行容器或本地推理执行/排队时延后，重启间隔至少 10 分钟。健康规则齐全即返回，不反复重启。[安装与停用指南](macos-boot-guard.md) 提供版本化 LaunchAgent 模板；本地槽位空闲不证明所有 HTTP/远端 AI 请求已结束。依据：[启动顺序决策](../.agents/notes/platform/20261014-wait-for-tailnet-colima-forwarding.md)；实例证据：[宿主重启记录](validation/20261014-host-reboot.md)。

## 验收与维护

**检查命令。** 绑定指定私网接口时，将健康检查地址换成该实际地址：

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml ps
docker compose -f docker-compose.yml -f docker-compose.cpu.yml logs --tail=100
curl -fsS http://127.0.0.1:8000/health
```

**验收范围。** 核对 `pipeline_ready`、实际 CPU Provider、生产网页和样例文件；再做真实本地换脸、工程保存、容器重启后恢复与同字节下载。不执行付费 AI 请求，回归结果与真实画质验收分开记录。清理只处理本次验收创建的工程/产物 ID，不清用户数据。

**更新与排错。** 先保存源码版本和镜像标识，再构建并沿用持久目录。`restart: unless-stopped` 随 Docker 运行时恢复容器，显式停止后须人工启动。故障先查 Colima/Docker 状态、容器日志和健康接口。

**实例证据。** [Mac ARM64 部署验收](validation/20261013-macos-cpu-docker.md) 记录输入、产物摘要及两级重启；[远程生产流程验收](validation/20261014-production-workflow.md) 记录 HTTP 工程恢复、默认素材、单图/批量下载、历史逐图反馈及最终镜像重启。[识别性能排障](validation/20261014-macos-face-performance.md) 分开测量远程上传与模型计算，取舍见 [性能排障决策](../.agents/notes/platform/20261014-separate-inference-from-tailscale-upload.md)。
