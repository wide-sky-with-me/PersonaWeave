# 固定 CI Runner 与动作提交，区分动作和应用运行时

日期：2026-10-14
状态：有效
范围：GitHub-hosted CI 环境与动作引用；不改变应用依赖、运行模型、Mac 部署或发布流水线。

## 背景

main 合并验收的作业全部成功，但 GitHub 提示旧动作的 Node 20 运行时被强制升级，以及 ubuntu-latest 即将迁移至 Ubuntu 26。维护者批准消除这些提示。CI 必须保留已有模型准备、摘要校验及前后端全量检查，环境维护不能成为跳过真实 API 夹具的理由，见 [资产准备决策](20261014-public-project-and-ci-assets.md)。

## 决策

两个作业明确使用 ubuntu-24.04。五个动作升级到核实过的稳定发布并用完整提交 SHA 固定，版本写在行内注释；逐个官方 action.yml 均确认 runs.using 为 node24。动作的 Node 24 不替换应用 Node 22；Python 3.11、pnpm 10 保持，uv 固定为已验证且 setup-uv 内置校验值覆盖的 0.12.23。Workflow 仅声明 contents: read，保留既有安装、缓存、模型验证、测试和构建步骤。

[CI YAML](../../../.github/workflows/ci.yml)是唯一可执行版本清单。本记录不复制一份 SHA 注册表。后续维护须先核实上游 tag 对应提交及输入兼容，再更新注释和 SHA，并运行实际远程 CI。OS 标签固定不意味着软件镜像永不更新；显式 OS 升级属于单独验收。

依据：[Runner 标签](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)、[完整 SHA 引用](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/find-and-customize-actions)。

## 被放弃的方案

- 用环境开关隐藏旧 Node 提示：没有更新实际动作，仍留下隐式迁移与上游支持问题。
- 继续 ubuntu-latest 或升级到 Ubuntu 26：前者让既有检查受自动 OS 切换影响，后者扩大此次善后范围。
- 使用浮动 major tag 或同步升级项目 Node/pnpm：前者使动作实现可在无仓库提交时改变，后者混淆动作运行时和已验证的应用基线。
- 放宽模型或安装前置：缓存不代替摘要校验，全套检查顺序与断言保留。

## 影响与验证

完整 SHA 需要显式维护，固定 OS 仍依赖 GitHub 对该镜像的支持。新增 [CI 元数据回归](../../../tests/test_public_project_documentation.py)约束触发器、权限、Runner、动作 SHA 及工具链；原模型校验先于 pytest、缓存目录/key 和前端 lint 的断言保留。静态回归不证明动作执行成功，实际结果以对应 [Actions](https://github.com/wide-sky-with-me/PersonaWeave/actions)为准。
