# 部署

安装、更新与回滚按目标平台文档执行，沿用同一组持久目录和匹配程序版本。

| 平台或问题 | 权威说明 |
| --- | --- |
| Linux/CUDA 源码开发 | [README](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/README.md#本地启动)、[依赖清单](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/pyproject.toml) |
| CPU/ARM64 与默认 CUDA 容器 | [Docker 部署](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/docker-deployment.md)、[Dockerfile](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/Dockerfile) |
| Apple Silicon / Colima | [Docker 平台说明](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/docker-deployment.md#apple-silicon--colima)、[登录守卫安装与停用](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/macos-boot-guard.md) |
| 尾网与 LAN 双入口、HTTP 部署 | [端口与访问](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/docker-deployment.md#端口与访问) |
| Windows CPU / NVIDIA GPU 便携版 | [Windows 指南](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/windows-portable.md) |
| 镜像、模型、素材与配置移交 | [维护手册](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#持久数据与资产清单) |
| 更新、停机备份与回滚 | [维护手册](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#更新与回滚) |

生产前端与 API 的部署边界、应用访问隔离及单进程约束以 [维护手册](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#访问与进程边界)为准。容器内健康、宿主端口转发和远端浏览器访问是不同检查项，实例证据见 [验证与交接](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/wiki/Validation-and-Handover.md)。

[返回首页](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/wiki/Home.md)
