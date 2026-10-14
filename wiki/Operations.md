# 运维

先确认实例版本、安装方式和真实入口，再按故障选章节。所有操作命令在 [运行维护手册](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md)维护，本页只做导航；备份、恢复与回滚的前提请连同命令一起阅读。

| 任务 | 章节 |
| --- | --- |
| 记录实例版本、入口和责任归属 | [实例登记与启动检查](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#实例登记与启动检查) |
| 判断访问隔离与单进程边界 | [访问与进程边界](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#访问与进程边界) |
| 配置可选 AI 并核对费用 | [AI 配置与调用费用](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#ai-配置与调用费用) |
| 核对数据库、图片、素材和权重 | [持久数据与资产清单](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#持久数据与资产清单) |
| 停机备份和副本恢复演练 | [备份](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#停机与一致性备份)、[恢复与迁移](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#恢复与迁移) |
| 更新、升级中断与回退 | [更新与回滚](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#更新与回滚) |
| 503/409/422、丢图、SQLite 锁和推理排队 | [故障排查](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/operations-runbook.md#故障排查) |

Mac/Colima 启动恢复、尾网/LAN 转发与容器出站边界见 [Docker 部署](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/docs/docker-deployment.md)。恢复是否成功，分别核对实际入口、数据和演练证据；文档存在不代表操作已经执行。

[返回首页](https://github.com/wide-sky-with-me/PersonaWeave/blob/main/wiki/Home.md)
