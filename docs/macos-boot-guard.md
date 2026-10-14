# Mac 登录启动守卫安装

适用于 [Docker 部署](docker-deployment.md)中的专用 Colima VM；[守卫脚本](../scripts/macos-colima-boot-guard.sh)只修复绑定地址晚于 VM 启动造成的地址转发规则缺失，保持 Homebrew 对 Colima 的管理。用户登录前、FileVault 解锁前不在覆盖范围内；实例故障证据见 [重启记录](validation/20261014-host-reboot.md)。

## 前提与检查边界

Colima 已启用 `network.hostAddresses`，Homebrew 登录服务已配置，应用是 VM 中唯一运行容器。绑定地址、部署目录和容器名称以实例实际配置为准；脚本支持的覆盖变量查实现，不把某次记录中的 IP 或用户名硬编码到新机器。

守卫只观察 `/health`的 `pipeline_ready`及 `local_inference`槽位。它不是全部 HTTP、工程写入或远端 AI 请求的活动探测器：跨图 VLM 仲裁在释放本地槽位后仍可能等待上游，健康空闲不保证该请求结束。仅在确认实例没有其他操作的登录恢复窗口启用自动修复；计划运行这些操作时先停守卫，待操作完成及地址规则正常后再启用。健康规则齐全时守卫直接返回；缺失规则触发重启仍须遵守这一边界。证据与取舍见 [空闲判定决策](../.agents/notes/platform/20261014-scope-boot-guard-idleness.md)。

## 安装

模板在 [LaunchAgent plist](../scripts/macos-colima-boot-guard.plist)。它的路径和地址占位符须按实例替换后才能加载。已有同名守卫时核对并沿用原安装，不重复创建；下面的生成过程在文件存在时停止。

在 Mac 的仓库根目录执行，先在终端 export 实际的 `PERSONAWEAVE_DEPLOYMENT_DIR`绝对路径及 `PERSONAWEAVE_BIND_ADDRESS`。Homebrew 工具位于 `/opt/homebrew/bin`；不同安装位置或容器名在生成 plist 后按脚本的覆盖变量明确设置。

```bash
set -eu
: "${PERSONAWEAVE_DEPLOYMENT_DIR:?请 export 实际部署绝对目录}"
: "${PERSONAWEAVE_BIND_ADDRESS:?请 export 实际绑定地址}"
test -d "$PERSONAWEAVE_DEPLOYMENT_DIR"
test ! -e "$PERSONAWEAVE_DEPLOYMENT_DIR/colima-boot-guard.sh"
plist="$HOME/Library/LaunchAgents/io.personaweave.boot-guard.plist"
test ! -e "$plist"
uv --no-config run --no-project --python /usr/bin/python3 - "$plist" <<'PY'
import ipaddress
import os
import plistlib
import sys
from pathlib import Path

root = Path(os.environ["PERSONAWEAVE_DEPLOYMENT_DIR"])
if not root.is_absolute() or not root.is_dir():
    raise SystemExit("Deployment directory must be an existing absolute directory")
address = str(ipaddress.IPv4Address(os.environ["PERSONAWEAVE_BIND_ADDRESS"]))
with Path("scripts/macos-colima-boot-guard.plist").open("rb") as stream:
    config = plistlib.load(stream)
config["ProgramArguments"][1] = str(root / "colima-boot-guard.sh")
config["EnvironmentVariables"]["PERSONAWEAVE_BIND_ADDRESS"] = address
config["EnvironmentVariables"]["PERSONAWEAVE_DEPLOYMENT_DIR"] = str(root)
config["StandardOutPath"] = config["StandardErrorPath"] = str(root / "boot-guard.log")
output = Path(sys.argv[1])
output.parent.mkdir(parents=True, exist_ok=True)
with output.open("xb") as stream:
    plistlib.dump(config, stream)
PY
cp scripts/macos-colima-boot-guard.sh "$PERSONAWEAVE_DEPLOYMENT_DIR/colima-boot-guard.sh"
chmod 755 "$PERSONAWEAVE_DEPLOYMENT_DIR/colima-boot-guard.sh"
/usr/bin/plutil -lint "$plist"
launchctl bootstrap "gui/$(id -u)" "$plist"
launchctl print "gui/$(id -u)/io.personaweave.boot-guard"
```

模板定义 30 秒周期和登录后启动；具体重启条件、600 秒冷却与失败退出由脚本决定。安装失败时保留错误与本次生成的文件，核对后再加载，不删除已存在的用户安装。示例生成和 plist 结构由 [安装回归](../tests/test_macos_boot_guard_installation.py)检查，Linux 检查不证明 macOS 的 launchd、实际服务重启或目标机器安装成功。

## 停用、恢复与移除

`launchctl bootout "gui/$(id -u)/io.personaweave.boot-guard"`停用守卫，不停止原 Homebrew Colima 服务。恢复使用 `launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.personaweave.boot-guard.plist"`，并核对 label、周期退出状态和实际宿主端口。

正式移除先 bootout，再仅移除本次守卫专属 plist 和部署目录内 `colima-boot-guard.sh`；日志和 `.boot-guard/last-restart`按实例保留策略处理，不清理应用数据或 VM。此操作不属于代码发布过程，发布代码不会自动安装、启动或停用该服务。
