# Wiki 维护与发布

先区分阅读、编辑源稿与发布独立 Wiki。只整理文档时，编辑对应源码文件并运行离线检查即可；下面的同步命令仅用于单独确认的发布任务，文档修改不等于发布。

Wiki 的维护源在 [wiki/](../wiki/Home.md)，与代码和专题文档一起版本化。它只提供导航，事实、接口和操作链接到所属文档。能力变更先更新权威来源，不复制状态机或字段清单。首页与专题使用完整仓库链接，`_Sidebar.md`负责独立 Wiki 页面导航。

## 可用性与首次初始化

**本项目状态**：仓库公开，正式 [GitHub Wiki](https://github.com/wide-sky-with-me/PersonaWeave/wiki)可公开读取并提供专题导航，源码也可匿名阅读。发布版本与核验方式见 [正式发布记录](validation/20261014-github-wiki.md)。后续维护使用普通快进同步，不重复初始化，不提交或传递浏览器凭据。

**其他仓库的前提**：Wiki 开关、独立 Git 仓库和已发布页面是不同状态。`has_wiki: true`不证明页面可读。GitHub [官方说明](https://docs.github.com/en/communities/documenting-your-project-with-wikis/adding-or-editing-wiki-pages)要求通过网页创建首个页面后再克隆 `<仓库名>.wiki.git`；有写权限的已登录维护者先创建并保存 `Home`，再同步。

版本化 [首页](../wiki/Home.md)与正式页面链接到同一事实来源。页面不可读时先核对开关、初始化和发布记录，不把源码可读当作独立页面发布证据。

## 同步页面

**发布前提**：使用已配置的 Git 凭据，在项目根目录操作。代码与文档须提交并推送到 `origin/main`，工作区干净且 HEAD 与远端一致。任一检查失败，停止同步，先处理源码版本或远端状态。

命令先克隆独立 Wiki，仅覆盖源目录管理的同名文件，保留其他远端页面。临时目录退出时清理；使用普通快进推送，不强制覆盖历史。

```bash
set -eu
repo_root="$(pwd -P)"
wiki_remote="https://github.com/wide-sky-with-me/PersonaWeave.wiki.git"
test -f "$repo_root/wiki/Home.md"
test -z "$(git status --porcelain)"
git fetch origin main
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
git ls-remote "$wiki_remote" HEAD
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
git clone "$wiki_remote" "$work/wiki"
cp "$repo_root"/wiki/*.md "$work/wiki/"
git -C "$work/wiki" add -- '*.md'
git -C "$work/wiki" diff --cached --check
if git -C "$work/wiki" diff --cached --quiet; then
    printf '%s\n' 'Wiki pages already match the source.'
else
    git -C "$work/wiki" diff --cached --stat
    git -C "$work/wiki" commit -m "docs: sync PersonaWeave wiki navigation"
    git -C "$work/wiki" push origin HEAD
fi
local_head="$(git -C "$work/wiki" rev-parse HEAD)"
remote_head="$(git ls-remote "$wiki_remote" HEAD | cut -f1)"
test "$local_head" = "$remote_head"
```

**预期结果**：页面字节相同则跳过提交；有差异则提交并快进推送，末尾核对本地与远端 HEAD。完成后仍须检查实际网页，不能只看源码推送结果。

**失败分支**：推送非快进时核对远端变更并合并，不用 `--force`。删除或更名受管页面另审查入链与远端差异；上述命令不删除未知远端页，也不自动清理源目录已移除的页面。

## 检查与记录

发布前先做离线检查，覆盖范围见 [Wiki 回归](../tests/test_wiki_documentation.py)与 [文档一致性回归](../tests/test_documentation_consistency.py)：

`uv run pytest tests/test_wiki_documentation.py tests/test_documentation_consistency.py tests/test_documentation_governance.py`

对同步命令执行 `bash -n`。这些检查验证源码导航、链接、长度和命令语法，不访问独立 Wiki，也不证明远端页面与源码一致。

源码推送成功不等于独立 Wiki 发布成功。发布记录分别保存代码 commit、Wiki remote HEAD、实际网页、检查结果和未执行项。文档整理或仅离线检查时，明确记为未执行独立 Wiki 发布。

证据分别见 [Wiki 核对记录](validation/20261014-github-wiki.md)、[公开设置与初始化核对](validation/20261014-public-repository.md)、[首次私有提交核对](validation/20261014-repository-publication.md)。各记录只证明其中的版本与范围，不代替本次发布核验。
