# GitHub Wiki 正式发布核对

日期：2026-10-14（Asia/Shanghai）。维护者通过 GitHub 网页创建首页后，独立 Wiki 可克隆并正常推送。本记录追加正式发布证据，不改写 [公开前后的初始化失败记录](20261014-public-repository.md#独立-wiki)。

## 版本与范围

- 源码 commit：`9a984cfc6f814b5407387c55866cd53f695b8991`，发布前确认工作树干净且与 `origin/main`一致。
- 初始化 Wiki commit：`d94f1be6174f48a489a731a0ee5e0b60f5eadebf`，首页为默认欢迎占位内容。
- 发布 Wiki commit：`37fed96b5a78fbcc35e0e8dcb7027e48216f04d2`，正常快进推送到独立仓库 `master`，保留初始化提交。
- 发布内容：源码 [wiki/](../../wiki/Home.md)中的 11 个 Markdown 文件，包含首页、8 个专题、侧栏与页脚；事实仍链接到所属源码/专题文档，不复制接口和状态机。

正式入口为 [GitHub Wiki](https://github.com/wide-sky-with-me/PersonaWeave/wiki)，固定首页为 [Home](https://github.com/wide-sky-with-me/PersonaWeave/wiki/Home)。仓库主页字段已指向正式 Wiki，源码中的 Wiki 文件仍保留为可版本化的维护源。

## 核验方式

发布前执行 `uv run pytest tests/test_wiki_documentation.py tests/test_documentation_consistency.py tests/test_documentation_governance.py tests/test_public_project_documentation.py -q`，结果 `34 passed`；Wiki staged diff 检查通过。

只覆盖源码目录管理的同名页面，不删除未知远端页面，不强推、不改写初始化历史。推送后通过不带 Git 凭据的 `ls-remote`核对远端 HEAD 与发布 commit 相等，检查初始化提交是发布 commit 的祖先，并逐文件比较 11 个页面与源码字节一致。

本次仅发布 Wiki 导航和更新文档入口，没有修改生产服务、实际 AI 配置、模型、用户数据或运行实例。后续维护按 [Wiki 发布说明](../wiki-publication.md)普通同步；新增/删除页面先检查源码入链和远端差异。
