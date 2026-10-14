# 代码、文档与 Wiki 提交核对

日期：2026-10-14（Asia/Shanghai）。任务是将实际工作区中的代码和配套文档提交到既有远程，并附可维护的 Wiki。

## 范围与基线

开工 HEAD 为 `f0d5fbccaf2df56e3a59928dd65e7031e6aea704`，分支 `main`，远程为 `https://github.com/wide-sky-with-me/PersonaWeave.git`；远程 `main`与开工 HEAD 一致。仓库为 private，既有 GitHub CLI 身份具备 push 权限，未改变仓库可见性。

保留并核对既有改动：CPU/ARM64 容器、LAN 覆盖、Mac 网络及启动工具、HTTP 浏览器摘要/UUID 兼容、AI 就绪控制、手机画笔布局，以及各自测试、决策和实例验证记录。本任务补充 [Wiki 源文件](../../wiki/Home.md)、[发布说明](../wiki-publication.md)、[Wiki 导航回归](../../tests/test_wiki_documentation.py)及 README/Changelog 入口。各历史实测保持原有日期和结论，本记录不把它们解释成本次新执行的目标机器验收。

## 已执行检查

- `uv run ruff format .`：236 个既有 Python 文件无需改动；`uv run ruff check .`通过。
- 定向 Python 回归：文档一致性/治理、容器依赖、Clash 和 Colima 守卫，共 49 passed。
- `pnpm --dir web format`与 `pnpm --dir web lint`通过；完整 Vitest 为 43 个文件、244 passed；`pnpm --dir web build`通过。环境为 Python 3.11.11、Node 24.21.0、pnpm 11.7.0，不冒充 CI 使用的 Node 22/pnpm 10 验收。
- 前端测试有 jsdom 未安装 canvas 的环境提示及预期失败响应日志，命令退出码为 0；这些输出不等于真实浏览器绘图验收。

补充 Wiki 与守卫安装材料后执行最终 Ruff format/check及七组定向 pytest，共 59 passed；Wiki、Mac 安装的 bash 围栏语法、守卫 `sh -n`、Clash `node --check`及 `git diff --check`通过。安装示例测试仅在临时目录生成/读取 plist，覆盖路径含 XML 特殊字符、保留已有文件、无效地址和目录；未调用 launchd 或 Homebrew。

## 远程 Wiki 状态

GitHub API 确认 `has_wiki: true`，但普通 Git 和显式 GitHub CLI 鉴权的 `ls-remote`均返回 `Repository not found`，没有取得独立 Wiki 的 HEAD。已在隔离临时目录提交全部 Wiki 页面并尝试首次普通推送，仍返回同一错误（推送退出码 128），临时仓库随命令退出清理，没有记录为远端发布。此响应不足以单独区分首页面尚未初始化与私有仓库计划限制；本次查询未取得账号计划信息，不虚构原因。

GitHub [官方说明](https://docs.github.com/en/communities/documenting-your-project-with-wikis/adding-or-editing-wiki-pages)明确首页面需先通过网页创建，再克隆 `.wiki.git`；私有 Wiki 的可用性受计划限制。版本化 Wiki 在代码仓库可阅读；独立 Wiki 只有推送成功、取得 HEAD 并核对页面后才记录为已发布。首次初始化和后续普通快进同步方式见 [发布说明](../wiki-publication.md)。

## 验证边界

本次不读取实际 `.env`或私人凭据，不改部署实例，不执行付费 AI、不构建完整 Docker/Windows 包。未对业务数据库和个人照片执行测试；后端仅跑列出的定向回归，不声称全量 Python、真实浏览器、模型画质或新机器重启验收通过。权重、素材、结果、运行数据库与本地构建产物保持在 Git 忽略范围内。

只读代码审查发现 Colima 守卫的空闲判断不覆盖槽位释放后仍在等待上游的 VLM 仲裁，存在缺失转发规则时打断该请求的风险；本次没有修改守卫或应用活动管理。已更正部署声明，补齐 [安装与停用指南](../macos-boot-guard.md)、LaunchAgent 模板和限制 [决策](../../.agents/notes/platform/20261014-scope-boot-guard-idleness.md)，只用于人工确认没有其他操作的恢复窗口。历史验收脚本与原始诊断报告属于实例资产，单独移交要求写入 [维护手册](../operations-runbook.md)。

既有实测含实例地址、用户名和部署路径，作为 private 仓库内的复现证据保留，不视为凭据；未来改变仓库或 Wiki 可见性须另做部署标识脱敏。对待提交文本检查私人密钥、GitHub token 和常见 API key 模式，未发现匹配；模式扫描不是所有敏感数据形式的完备证明。

代码和 Wiki 源文件的交付提交可通过 `git log -- docs/validation/20261014-repository-publication.md`定位，远端状态以实际 `refs/heads/main`核对，不用本文中的自引用哈希作为证据。
