# 公开仓库与协作内容核对

日期：2026-10-14（Asia/Shanghai）。按维护者请求将 `wide-sky-with-me/PersonaWeave`从 private 改为 public，并补齐公众阅读及协作入口。开工 commit 为 `7b93505f047f24c66be2ca51d56f6ddf06c2cc9c`，此前提交事实保留在 [原提交记录](20261014-repository-publication.md)。

## 设置与公开范围

已通过 GitHub CLI 更新 visibility、中文 description、文档主页和主题，启用 Issues/Wiki；GitHub API 返回 `private=false`、`visibility=public`，不带身份的仓库 API 请求也成功。私密漏洞报告 API 返回 `enabled=true`，报告方式见 [SECURITY](../../SECURITY.md)。未更改应用部署、端口、用户登录或付费 AI 配置。

公开范围是源码、文档及已托管的四张夹具，不上传权重、个人素材、数据库、`.env`、结果或本机构建包。公开前扫描 26 个可达提交的 1044 个历史 blob，未命中私人密钥、GitHub token、常见 API key 和 AWS access key 模式；模式扫描不保证覆盖所有秘密形式。四张夹具均无 EXIF 项或 PNG 附加 metadata。未读取实际凭据、用户数据库或未托管个人照片。

Git 历史没有改写。既有带日期的实例证据保留其部署地址、用户名及路径，这些标识不属于凭据；不把新文档使用的脱敏夹具地址解释为历史也已脱敏。账号计划、未纳管诊断原始报告和本地便携包没有随公开设置发布。

## 内容补齐

新增 [公开项目说明](../public-project.md)、[CONTRIBUTING](../../CONTRIBUTING.md)、[SECURITY](../../SECURITY.md)、Issue 表单及 PR 模板；README 加入动态 CI 徽章、公开入口并精简重复细节。Wiki 增加 [公开项目与协作](../../wiki/Community.md)，沿用链接权威文档的导航层，不复制能力清单。

顶层源码许可保持未指定：仓库公开不是维护者选择 MIT/Apache，也不代替模型和素材的条款。[公开说明](../public-project.md#源码与资产许可)明确这一状态。本次没有创建假定授权的 LICENSE 或伪造不存在的公开发行附件。

将当前文档中指向未托管 `data/portrait_expansions/README.md`的两条链接改为 [版本化文本摘录](20261014-portrait-reference-evidence.md)。摘录保留原有摘要并注明原实验日期、逐张指标和完整命令未记录；没有重新生成图片或外推质量结论。

## CI 失败及修复范围

实际读取 [38513399223](https://github.com/wide-sky-with-me/PersonaWeave/actions/runs/38513399223)日志：后端为 `1 failed, 871 passed, 8 errors`，依赖安装/Ruff通过；前端安装、测试和构建通过。一个 failure 来自上述两条未托管文档链接，八个 setup errors 来自同一真实 CPU API 夹具启动前没有模型；缺 `det_10g.onnx`后尝试兼容文件名 `scrfd_10g_bnkps.onnx`仍缺失，不是必须重命名模型。该次失败并非计费或权限启动阻断。

[CI 工作流](../../.github/workflows/ci.yml)在 pytest 前执行现有 [下载器](../../scripts/download_models.py)，每次核对固定摘要，并按下载器内容键缓存模型；不提交 ONNX、不修改检测器、不跳过 API 回归。前端增加现有 `pnpm lint`。新增 [公开资料回归](../../tests/test_public_project_documentation.py)覆盖协作入链、文档不依赖忽略运行目录、表单必填/唯一字段、CI 模型准备顺序及夹具 metadata。

本次 `uv run ruff format .`与 `uv run ruff check .`干净；文档/公开资料/Wiki/下载器检查为 `41 passed`，`git diff --check`通过，README 为 6427 字符，低于 6500 上限。

后端全量在临时隔离源码副本执行：只复制 Git 管理及本次新增源文件，不复制 `.env`、用户数据或输出；复用已安装依赖和已下载的六个模型，对模型运行原下载器摘要校验后，显式设置测试 AI URL/key，验证源码导入位于隔离树。`uv run pytest -q`得到 `887 passed in 109.46s`，临时源码、数据库和输出随后清理。该结果验证真实 CPU API 与全量回归，但不是新环境依赖解析、冷缓存模型下载、真实 AI 服务或成片画质验收。

远端本次新提交的 CI 在推送后单独核对；不把本地通过或 YAML 步骤存在当作 GitHub 冷缓存构建成功。

## 独立 Wiki

仓库公开后用已配置的 GitHub Git 凭据再次普通推送本仓库管理的 11 个 Wiki 源文件到 `HEAD:master`，Git 返回 `remote: Repository not found.`及退出码 128；没有可验证的独立 Wiki HEAD，独立页面未发布。未强制推送或改写远端历史，临时 Wiki 仓库已清理。版本化源文件仍可匿名阅读。

[GitHub 官方说明](https://docs.github.com/en/communities/documenting-your-project-with-wikis/adding-or-editing-wiki-pages)允许公开仓库使用免费 Wiki，并要求先在网页创建首个页面；`has_wiki=true`不足以证明已初始化。[发布说明](../wiki-publication.md)保留创建 `Home`页后的普通同步步骤，不获取或传递浏览器身份。
