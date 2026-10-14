# 漏洞与数据暴露报告

## 私下报告

请使用 [GitHub 私密漏洞报告入口](https://github.com/wide-sky-with-me/PersonaWeave/security/advisories/new)提交涉及任意文件访问、路径穿越、请求边界或凭据/个人图片暴露的问题；不要把可用密钥、业务数据库、用户照片或可直接利用真实部署的细节放到公开 issue。

提供受影响 commit/包版本、最小复现步骤、合成输入、预期边界、实际响应和影响范围。原始秘密留在自己的环境，报告中使用占位符；确需额外材料时由维护者在私密报告内约定渠道。此仓库没有公布固定响应时限。

## 检查与支持范围

修复以当前 `main`代码和可复现证据为基础；源码 commit、Windows 完整包/修复包和部署镜像分别标注，不能用源码修复推定旧包已更新。已发布版本、文件与摘要查 [发行入口](https://github.com/wide-sky-with-me/PersonaWeave/releases)及 [Windows 指南](docs/windows-portable.md)。没有长期支持版本矩阵或历史版本自动回补承诺。

## 部署边界

仓库公开只开放源码、文档和已托管夹具，不提供公共在线处理服务。应用没有完整登录和多租户隔离层；部署须按 [运行维护手册](docs/operations-runbook.md#访问与进程边界)控制网络、代理和实例访问。健康接口就绪不等于上游鉴权或整个服务空闲；Colima 守卫的覆盖限制见 [维护说明](docs/macos-boot-guard.md#前提与检查边界)。

权重、`.env`、个人素材、数据库、输出与诊断原始记录保持独立管理。一般使用问题通过 [Issues](https://github.com/wide-sky-with-me/PersonaWeave/issues)反馈，代码协作见 [贡献说明](CONTRIBUTING.md)。
