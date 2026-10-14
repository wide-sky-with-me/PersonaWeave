# 漏洞与数据暴露报告

## 私下报告

涉及任意文件访问、路径穿越、请求边界或凭据/个人图片暴露时，请使用 [GitHub 私密漏洞报告](https://github.com/wide-sky-with-me/PersonaWeave/security/advisories/new)。一般使用问题仍走 [Issues](https://github.com/wide-sky-with-me/PersonaWeave/issues)。

私密报告提供这些信息即可：

- 受影响的 commit/包版本及安装方式。
- 最小复现步骤、合成输入、预期边界和实际响应。
- 已观察到的影响范围。

真实密钥、业务数据库、个人图片和真实部署的可利用细节不进入公开 issue。秘密留在自己的环境，报告使用占位符；额外材料的渠道由维护者在私密报告内约定。此仓库没有固定响应时限。

## 检查与支持范围

修复依据当前 `main` 和可复现证据。请区分源码 commit、Windows 完整包/修复包及部署镜像：源码修复不意味着旧包或正在运行的镜像自动更新。

已发布文件与摘要查 [发行入口](https://github.com/wide-sky-with-me/PersonaWeave/releases)和 [Windows 指南](docs/windows-portable.md)。项目没有长期支持版本矩阵或历史版本自动回补承诺。

## 部署边界

仓库公开的是源码、文档和受控测试夹具，不是公共在线处理服务。应用没有完整登录和多租户隔离层；网络、代理及实例访问须按 [运行维护手册](docs/operations-runbook.md#访问与进程边界)控制。

健康就绪不代表上游鉴权成功，也不代表整个服务空闲。Colima 守卫的检查范围见 [维护说明](docs/macos-boot-guard.md#前提与检查边界)。

权重、`.env`、个人素材、数据库、输出和原始诊断记录独立管理。协作规则见 [贡献说明](CONTRIBUTING.md)，阅读入口见 [文档总览](docs/README.md)。
