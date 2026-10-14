# 部署

先选择实际安装方式；源码、CPU 容器与 Windows 包使用不同的依赖流程。首次安装按平台指南准备资产，更新和回滚沿用同一组持久目录与匹配程序版本。

| 平台或问题 | 权威说明 |
| --- | --- |
| Linux/CUDA 源码开发 | [README](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/README.md#本地启动)、[依赖清单](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/pyproject.toml) |
| CPU/ARM64 与默认 CUDA 容器 | [Docker 部署](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/docker-deployment.md)、[Dockerfile](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/Dockerfile) |
| Apple Silicon / Colima | [Docker 平台说明](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/docker-deployment.md#apple-silicon--colima)、[登录守卫安装与停用](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/macos-boot-guard.md) |
| 尾网与 LAN 双入口、HTTP 部署 | [端口与访问](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/docker-deployment.md#端口与访问) |
| Windows CPU / NVIDIA GPU 便携版 | [Windows 指南](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/windows-portable.md) |
| 镜像、模型、素材与配置移交 | [维护手册](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#持久数据与资产清单) |
| 更新、停机备份与回滚 | [维护手册](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#更新与回滚) |

更新前确认持久数据与配置已备份，部署约束见 [访问与进程边界](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#访问与进程边界)。容器健康、宿主转发和远端浏览器访问是三个独立检查，不能互相替代；实例范围见 [验证与交接](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/wiki/Validation-and-Handover.md)。

[返回首页](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/wiki/Home.md)
