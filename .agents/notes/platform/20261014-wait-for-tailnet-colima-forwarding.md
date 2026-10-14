# 在尾网地址就绪后校验 Colima 的宿主转发规则
状态：有效
日期：2026-10-14

## 背景

Mac 早上重启后，Homebrew 已自动启动 Colima，PersonaWeave 容器健康且虚拟机内监听正确，但宿主没有应用端口监听。启动日志明确记录 `Not forwarding TCP 100.88.228.28:8000`；生成的 Lima 规则只有启动当时的 LAN 地址，遗漏尾网地址。地址随后被复制到 VM，不会重新生成宿主转发规则。

## 决策

保留 Homebrew 对 Colima 的管理，以及仅绑定尾网接口的 Compose 配置。增加部署用户的 launchd 周期启动守卫；等待绑定地址、VM 和专用空闲容器就绪后，检查生成的双向地址规则，缺失时重启原 Colima service 以重新生成。配置可用时不操作服务；存在其他运行容器、推理排队或执行时延后，重启失败有冷却窗口与退出状态。

## 被放弃的方案

- 仅重新启动应用容器：没有重建 Lima 的宿主转发规则，故障层级不匹配。
- 发布到全部接口：绕过顺序依赖，但扩大无登录应用的监听边界。
- 每次改写生成的 Lima YAML：下次启动会重生成，且原生转发绑定仍依赖地址何时出现。
- 替换 Homebrew 自己的 LaunchAgent：服务管理或升级可覆盖修改，形成两个不一致的启动入口。
- 只手动重启 Colima：当次恢复，不覆盖后续的同类启动顺序。

## 影响与验证

这是宿主运维守卫，不参与换脸流水线，不改镜像、模型或持久数据。Homebrew service 与守卫均为登录后的用户服务，仍不承诺无人登录的整机冷启动。共享 VM 新增其他运行容器后，守卫会跳过自动重启，需要运维人员处理。

实现：[启动守卫](../../../scripts/macos-colima-boot-guard.sh)；回归：[启动顺序测试](../../../tests/test_macos_colima_boot_guard.py)。维护：[Docker 部署](../../../docs/docker-deployment.md)；实测：[宿主重启故障记录](../../../docs/validation/20261014-host-reboot.md)。Colima 的地址规则生成依据见 [官方实现](https://github.com/abiosoft/colima/blob/main/environment/vm/lima/yaml.go)。
