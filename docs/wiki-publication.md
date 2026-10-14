# Wiki 维护与发布

Wiki 源文件在 [wiki/](../wiki/Home.md)，与代码和专题文档一起提交。它只负责阅读导航，事实、接口和操作步骤链接到所属文档；修改能力时先更新权威来源，不在 Wiki 复制状态机或字段清单。首页及专题使用完整仓库链接，因此在源码仓库和独立 Wiki 中都能打开同一事实源；`_Sidebar.md`负责独立 Wiki 的页面导航。

## 可用性与首次初始化

项目仓库为公开状态，正式 [GitHub Wiki](https://github.com/wide-sky-with-me/PersonaWeave/wiki)已初始化并发布，源码仍可匿名阅读；发布版本和核对方式见 [正式发布记录](validation/20261014-github-wiki.md)。仓库 Wiki 开关、独立 Wiki Git 仓库和已发布页面是不同状态。GitHub [官方说明](https://docs.github.com/en/communities/documenting-your-project-with-wikis/adding-or-editing-wiki-pages)要求新建 Wiki 时先通过网页创建首个页面，再克隆 `<仓库名>.wiki.git`；`has_wiki: true`本身不证明页面已发布。

本项目已完成首次初始化，后续直接执行下面的普通快进同步；无需提交或传递浏览器凭据。其他尚未初始化的仓库，先在有写权限的已登录浏览器中创建 `Home`页并保存，再同步。版本化 [首页](../wiki/Home.md)与正式页面使用同一事实来源。

## 同步页面

使用已配置的 Git 凭据，在项目根目录执行。先检查代码和文档已提交且已推送到 `origin/main`；随后克隆独立 Wiki 并只覆盖本仓库管理的同名文件，保留远端其他页面。临时目录在退出时清理，推送使用普通快进，不强制覆盖远端历史。

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

修改只覆盖源目录中的页面，不删除未知远端页；移除或更名本仓库管理的页面须另外审查其入链及远端差异。并行修改导致推送非快进时，先核对远端变更再合并，不用 `--force`。

## 检查与记录

Wiki 链接、导航覆盖和事实文档链接检查见 [Wiki 回归](../tests/test_wiki_documentation.py)及 [文档一致性回归](../tests/test_documentation_consistency.py)。运行 `uv run pytest tests/test_wiki_documentation.py tests/test_documentation_consistency.py tests/test_documentation_governance.py`，并对同步命令执行 `bash -n`。

源码推送成功不等于独立 Wiki 发布成功。交付记录分别登记代码 commit、Wiki remote HEAD、实际网页及未执行项；正式发布见 [Wiki 核对记录](validation/20261014-github-wiki.md)，公开设置及初始化前的范围见 [公开记录](validation/20261014-public-repository.md)，首次私有提交见 [原提交核对](validation/20261014-repository-publication.md)。
