# 合并后 CI、Wiki 与分支善后验收

日期：2026-10-14。范围：维护者明确批准的 CI 维护、独立 Wiki 同步、已合并分支清理；Mac 部署与用户运行数据保持不动。

## 版本与结果

- 合并基线：`fcaad810d739efa23a3923c794505527022e5745`。
- CI 实现提交：`73cc88f5c4621457aebac6950cbe4a669be24ba8`；[远程 CI](https://github.com/wide-sky-with-me/PersonaWeave/actions/runs/38541358265)前后端均成功，两个 check-run 的 annotations_count 均为 0。
- Wiki 从 `37fed96b5a78fbcc35e0e8dcb7027e48216f04d2` 普通快进到 `997021a391bc240360e0f6a0e5f48ef0aca6d77e`；11 个受管源稿逐字节一致，9 个公开正文页通过 HTTP 与返回 HTML 核对。
- 本地和远程 `refactor/organization-and-boundaries` 均已删除；原提交 `194d5ac4ad35e884b30303dcfd425f47dca43f05` 在 main 历史中保留。

本记录固定实现提交与发布版本，不嵌入自身收尾提交的 SHA；收尾文档提交及其 CI 以 Git 历史和对应 Actions 为准。技术选择见 [CI 决策](../../.agents/notes/platform/20261014-ci-runner-baseline-and-action-pins.md)，当前配置以 [workflow](../../.github/workflows/ci.yml)为准。

## CI 验证

五个动作逐个通过官方 release API 解析 tag 对应提交，并读取该提交的 action.yml，均确认 Node 24 运行时。两作业使用 Ubuntu 24.04，保留应用 Python 3.11、Node 22、pnpm 10，uv 固定为 0.12.23。模型准备、摘要验证、缓存及完整 pytest 顺序保留；测试从固定旧 cache major 引用改为识别 cache 动作，并新增完整 SHA 与工具链护栏。

本地依次执行 `uv run --no-sync ruff format .`、`ruff check .`、相关回归；格式检查和 `git diff --check` 通过。相关回归 **73 passed in 1.87s**，覆盖公开资料、CI 元数据、文档一致性/治理/链接/导航、Wiki、Mac 守卫、交付契约、模型路由及强度契约。远程后端完整 **968 passed in 38.54s**；前端 frozen-lockfile 安装、lint、test、build 均成功。旧动作运行时和 ubuntu-latest 迁移提示均未出现在该运行的 check-run 注释中。

未修改业务源码、依赖清单或锁文件；未重复上一轮的 Wheel/CLI 安装验证或本地全量推理。远程 CI 是环境与回归证据，不是视觉质量、GPU 或实际部署验收。

## Wiki 发布与网页核对

从干净且与 origin/main 一致的源码提交发布，执行现有 [同步流程](../wiki-publication.md#同步页面)：克隆独立 Wiki，仅复制 wiki/*.md 同名源稿，检查暂存差异，提交后普通推送。没有删远端页面、改写历史或执行强制推送。重新 fetch 后逐个比较 origin/master 的文件字节，再核对本地与远端 HEAD；临时克隆目录退出时清理。

匿名读取 [首页](https://github.com/wide-sky-with-me/PersonaWeave/wiki)及 Architecture、Community、Deployment、Development、Getting-Started、Operations、Validation-and-Handover、Workflows，均为 HTTP 200。检查 wiki-body 标题及源稿链接、每页自定义侧栏的全部专题入口、wiki-footer；首页还核对任务导航表。未做浏览器截图或视觉排版验收。

首轮网页探针按原字符串比较中文锚点，Architecture 报差异；服务端 href 实际是同一锚点的百分号编码。确认返回值后，用 urllib.parse.unquote 规范化两侧 URL，再复核全部页面通过；未改源稿或放宽离线链接检查。

## 分支清理与边界

删除前精确核对本地、origin 跟踪引用和 ls-remote 均指向原分支提交，并用 git merge-base --is-ancestor 证明其已进入 origin/main。使用普通远程删除和 git branch -d；之后 for-each-ref 与 ls-remote 均确认引用缺失。main 保留结构、文档和合并提交，未执行强制删除或历史重写。

本轮未执行 SSH、Mac 服务/容器操作、Docker 镜像发布、版本发行或付费 AI 请求；没有读取实际 .env、个人素材或业务数据库。部署配置、Mac 守卫脚本和历史冻结记录保持原样。分支清理不等同于运行实例升级。
