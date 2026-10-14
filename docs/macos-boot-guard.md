# Mac 登录启动守卫安装

适用于 [Docker 部署](docker-deployment.md) 的专用 Colima VM。[守卫脚本](../scripts/macos-colima-boot-guard.sh) 仅修复绑定地址晚于 VM 启动造成的转发规则缺失，仍由 Homebrew 管理 Colima。仅覆盖用户登录后，不覆盖登录前或 FileVault 解锁前；实例证据见 [重启记录](validation/20261014-host-reboot.md)。

## 前提与检查边界

**前提。** Colima 启用 `network.hostAddresses`，Homebrew 登录服务已配置，应用是 VM 中唯一运行容器。部署目录、绑定地址和容器名按实例配置填写；覆盖变量查脚本，不照抄记录中的 IP/用户名。

**观察边界。** 守卫只读 `/health` 的 `pipeline_ready` 和 `local_inference` 槽位，不探测全部 HTTP、工程写入或远端 AI 活动。跨图 VLM（视觉语言模型）仲裁释放本地槽位后仍可能等待上游，健康空闲不代表请求结束。

**启用时机。** 仅在确认无其他操作的登录恢复窗口启用自动修复；计划执行上述操作时先停守卫，完成且地址规则正常后再恢复。规则齐全即返回；缺失规则的重启也须遵守此边界。依据：[空闲判定决策](../.agents/notes/platform/20261014-scope-boot-guard-idleness.md)。

## 安装

**模板。** [LaunchAgent plist](../scripts/macos-colima-boot-guard.plist) 是 macOS 登录任务配置，须替换路径/地址占位符后加载。已有同名守卫时核对并沿用，不重复创建；下面的生成过程遇到已有文件会停止。

**操作。** 在 Mac 仓库根目录，先 export 实际 `PERSONAWEAVE_DEPLOYMENT_DIR` 绝对路径和 `PERSONAWEAVE_BIND_ADDRESS`。默认 Homebrew 工具位于 `/opt/homebrew/bin`；其他安装位置或容器名，生成 plist 后按脚本覆盖变量设置。

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

**结果与排错。** 模板定义登录后启动、30 秒周期；脚本决定重启条件、600 秒冷却及失败退出。安装失败保留错误与本次生成文件，核对后再加载，不删除已有用户安装。[安装回归](../tests/test_macos_boot_guard_installation.py) 仅检查生成示例和 plist 结构；Linux 检查不证明 macOS launchd、实际重启或目标安装成功。

## 停用、恢复与移除

**停用。** `launchctl bootout "gui/$(id -u)/io.personaweave.boot-guard"` 只停守卫，不停原 Homebrew Colima 服务。

**恢复。** `launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.personaweave.boot-guard.plist"`，随后核对 label、周期退出状态及宿主实际端口。

**移除。** 先 bootout，仅删除本次专属 plist 和部署目录内 `colima-boot-guard.sh`；日志与 `.boot-guard/last-restart` 按实例策略保留，不清应用数据或 VM。代码发布不自动安装、启动或停用该服务。
