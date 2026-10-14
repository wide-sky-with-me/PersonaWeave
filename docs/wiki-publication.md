# Wiki 维护与发布

Wiki 源文件在 [wiki/](../wiki/Home.md)，与代码和专题文档一起提交。它只负责阅读导航，事实、接口和操作步骤链接到所属文档；修改能力时先更新权威来源，不在 Wiki 复制状态机或字段清单。首页及专题使用完整仓库链接，因此在源码仓库和独立 Wiki 中都能打开同一事实源；`_Sidebar.md`负责独立 Wiki 的页面导航。

## 可用性与首次初始化

仓库 Wiki 开关、独立 Wiki Git 仓库和已发布页面是不同状态。GitHub [官方说明](https://docs.github.com/en/communities/documenting-your-project-with-wikis/adding-or-editing-wiki-pages)要求先通过网页创建首个页面，再克隆 `<仓库名>.wiki.git`。私有仓库的 Wiki 还受账号/组织计划限制，不能只凭 `has_wiki: true`宣称页面可用。

在有仓库写权限的已登录浏览器中打开 [Wiki](https://github.com/wide-sky-with-me/PersonaWeave/wiki)，创建 `Home`页并保存；若页面显示计划限制，保留私有可见性，在符合该限制后初始化。无需提交或传递浏览器凭据。版本化 [首页](../wiki/Home.md)在独立 Wiki 初始化前即可从仓库阅读。

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

源码推送成功不等于独立 Wiki 发布成功。交付记录分别登记代码 commit、Wiki remote HEAD、实际网页及未执行项；本次记录见 [提交核对](validation/20261014-repository-publication.md)。
