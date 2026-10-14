# Docker 部署

同一容器提供生产前端和 API，默认服务端口为 8000。构建与启动来源是 [Dockerfile](../Dockerfile)、[Compose](../docker-compose.yml) 和 [容器入口](../docker-entrypoint.sh)，不是 Vite 开发服务器。

## 镜像选择

源码开发锁文件包含 Linux/CUDA 依赖。Dockerfile 的默认 `backend` 目标使用该锁文件；纯 CPU 环境可使用 `cpu` 目标及 [CPU Compose 覆盖](../docker-compose.cpu.yml)。

```bash
# 在项目根目录执行；已有 .env 时保留它。
test -e .env || cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up --build -d
```

CPU 目标通过 [container_requirements.py](../scripts/container_requirements.py) 从 `uv export` 保留非 CUDA 版本和环境标记，排除 NVIDIA 依赖，固定使用提供 Linux ARM64 wheel 的 `onnxruntime==1.23.2`，只保留 headless OpenCV；不改开发环境或 uv.lock。安装使用 `uv --no-config pip install --no-deps`，隔离开发配置中排除 CPU runtime 的覆盖规则，并避免项目元数据把 CUDA 依赖带回 CPU 镜像。导出遇到未锁定依赖、缺失/冲突的 GPU runtime 或缺失 headless OpenCV 时直接停止。构建同时验证主要模块导入及 CPU Provider。回归见 [容器依赖测试](../tests/test_container_requirements.py)，取舍见 [CPU runtime 决策](../.agents/notes/platform/20261013-pin-cpu-runtime-and-ignore-dev-overrides.md)。

## 数据与模型

Compose 将项目内的 `models/`、`data/`、`outputs/` 挂载到容器相同目录。容器重建不覆盖持久数据；移动安装或备份时停止写入并保留这三个目录及服务配置。不要把个人 `.env`、数据库、照片和结果加入镜像或公共分发包；`.dockerignore` 负责排除它们。

人物素材库须独立安装完整的数据快照或按 manifest 导入；挂载空目录不会预置人物。迁移与新实例的要求见 [素材库维护](portrait-library.md#新实例与迁移)。

首次启动缺少必需模型时，入口运行校验摘要的下载器。离线部署可先按 [模型说明](../models/README.md) 验证并复制模型缓存；文件存在本身不证明完整性。测试样例来自 [版本化夹具](../tests/fixtures/README.md)，容器镜像不包含这些图片；需要示例按钮时，将四张夹具按 Windows 构建脚本的 `SAMPLES` 映射放入 `data/test_target/` 和 `data/test_images/`。

AI 服务通过远端自己的 `.env` 配置，不从另一台机器自动复制实际凭据；按用户明确选定的来源迁移时，仅私下导入所需六项并保留原文件。字段和协议前缀见 [模型配置](model-catalog.md)。配置缺失时，已存会话和本地换脸仍可用，AI 就绪为 false；健康检查不验证付费上游服务。Mac 实例的配置、只读鉴权与网页证据见 [AI 与网络验收](validation/20261014-remote-ai-configuration.md)。

## 端口与访问

`PERSONAWEAVE_BIND_ADDRESS` 与 `PERSONAWEAVE_PORT` 控制 Compose 的宿主机发布地址及端口，默认保持全接口 8000；容器内始终使用 8000。同源前端访问同一端口的 API。内部使用可把绑定地址设置为实际私网接口；仅本机使用可设为 `127.0.0.1`。服务本身没有登录隔离，不应直接暴露到公网。

同时保留尾网和 LAN 地址时，设置原绑定地址，并在实例 `.env` 填写实际 `PERSONAWEAVE_LAN_ADDRESS`。追加 [LAN 覆盖](../docker-compose.lan.yml)：`docker compose -f docker-compose.yml -f docker-compose.cpu.yml -f docker-compose.lan.yml up --no-build -d`；已有 CPU 镜像才使用 `--no-build`。该覆盖只追加指定地址，不改镜像、数据或原入口。设置 `COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml:docker-compose.lan.yml` 可让普通 Compose 调用保留全部覆盖；显式 `-f` 调用须包含 LAN 文件。DHCP 地址变化时更新变量；Colima 须核对新地址已进入转发规则，再重建容器。保持路由器 DHCP 租约可减少地址变动；客户端隔离或防火墙仍须用实际连接验证。取舍见 [双入口决策](../.agents/notes/platform/20261014-explicit-lan-and-tailnet-bindings.md)，当前实例实测入口见 [LAN 验收](validation/20261014-lan-access.md)。

## Apple Silicon / Colima

macOS 使用 Linux 虚拟机运行 ARM64 容器，可通过 Homebrew 安装 `colima`、`docker`、`docker-compose` 和 `docker-buildx`。Docker CLI 需要能发现 Homebrew 的 Compose/Buildx 插件。Colima 使用 VZ 和 ARM64 架构；CPU、内存、磁盘按目标机器容量配置，不把实例分配当作已验证的最低硬件配置。

构建代理分两层：镜像仓库连接由 Docker daemon 配置，构建阶段使用 Docker 的 `HTTP_PROXY` / `HTTPS_PROXY` build args；参见 [Docker daemon 代理](https://docs.docker.com/engine/daemon/proxy/)。前端阶段开启 `NODE_USE_ENV_PROXY=1`，要求 Node 22.21+，让 Corepack 和 Node fetch 显式使用代理；参见 [Node 环境代理](https://nodejs.org/api/http.html#built-in-proxy-support)。这些设置只影响构建，不把代理地址或凭据固化到应用运行环境。故障检查需确认 VM 的 `/etc/resolv.conf` 实际有效，而不是仅检查宿主机 DNS。

运行环境的出站代理须单独在实例 `.env` 配置，并用 NO_PROXY 保留回环和应用尾网地址。Mac 使用 Clash Verge 时，DNS 假 IP 可让健康容器仍连不上上游；该实例通过 [全局扩展](../scripts/macos-clash-tailnet.js) 将尾网与所配 AI 域名返回真实 IP，前置尾网 DIRECT 并从 TUN 排除其网段，其余代理设置保留。语义及限制见 [网络决策](../.agents/notes/platform/20261014-tailnet-real-dns-clash.md)。Clash DIRECT 不代表每个对端均完成 UDP 打洞；国内控制/DERP 是否可达须实测，必要的域名代理例外放在进程直连之前。

[Colima 配置](https://colima.run/docs/configuration/) 的 `network.hostAddresses` 支持将 Mac 的实际接口地址复制到 VM，再发布到指定地址。挂载目录放在该用户家目录下；[Colima FAQ](https://colima.run/docs/faq/)说明默认共享目录边界。实际访问仍须从另一台机器验证，不能仅用容器内 localhost 检查替代。

普通 CPU ONNX 容器不使用 Apple Metal/CoreML GPU。Docker Model Runner 的 Metal 支持属于其专属推理引擎，不是本项目 ONNX 的 GPU 后端；见 [Docker GPU 边界](https://docs.docker.com/desktop/features/gpu/)。Colima 的登录自启动可使用 `brew services start colima`；这是登录后的启动，不保证没有用户登录的冷启动可用。

指定私网地址的 Colima 转发规则依赖启动时该地址已存在。仅有 `hostAddresses: true` 和健康容器不代表宿主发布成功；须核对生成规则与 Mac 上的实际端口监听。Mac 实例安装了登录后的 [启动守卫](../scripts/macos-colima-boot-guard.sh)：每 30 秒确认绑定地址及 VM 就绪，地址转发规则缺失时重启原 Homebrew service；有其他运行容器或本地推理在执行或排队时延后，重启间隔至少 10 分钟。健康配置直接返回，不反复重启。[安装与停用指南](macos-boot-guard.md)提供版本化 LaunchAgent 模板，并说明本地槽位空闲不保证全部 HTTP/远端 AI 请求已结束。启动顺序取舍见 [决策](../.agents/notes/platform/20261014-wait-for-tailnet-colima-forwarding.md)，安装实例与证据见 [宿主重启记录](validation/20261014-host-reboot.md)。

## 验收与维护

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml ps
docker compose -f docker-compose.yml -f docker-compose.cpu.yml logs --tail=100
curl -fsS http://127.0.0.1:8000/health
```

绑定到指定私网接口时，健康检查命令使用该实际地址。验收检查 pipeline_ready、实际 CPU Provider、生产网页及样例文件，随后执行一次真实本地换脸、工程保存、容器重启后恢复及同字节下载。不执行付费 AI 请求；回归通过与真实画质验收分开记录。删除验收工程及产物只处理本次创建的 ID，不清理用户数据。

更新先保存当前源码版本和镜像标识，再重新构建；沿用原持久目录。`restart: unless-stopped` 让容器随 Docker 运行时恢复，但显式停止后需人工启动。故障优先查 Colima/Docker 状态、容器日志及健康接口。

带输入、产物摘要和两级重启证据的实例记录见 [Mac ARM64 部署验收](validation/20261013-macos-cpu-docker.md)；HTTP 工程恢复、默认素材、实际单图与批量下载、历史逐图反馈及最终镜像重启见 [远程生产流程验收](validation/20261014-production-workflow.md)。远程上传与模型计算分开测量的案例见 [识别性能排障](validation/20261014-macos-face-performance.md)，操作取舍见 [性能排障决策](../.agents/notes/platform/20261014-separate-inference-from-tailscale-upload.md)。
