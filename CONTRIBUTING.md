# 参与贡献

PersonaWeave 接收可复现的问题报告、文档修正和有明确范围的改动。使用问题与功能建议提交到 [Issues](https://github.com/wide-sky-with-me/PersonaWeave/issues)；涉及凭据、任意文件访问或个人图片泄露的报告按 [漏洞报告说明](SECURITY.md)私下提交。项目能力、发行入口与许可边界见 [公开项目说明](docs/public-project.md)。

## 提交问题

先搜索已有问题，选择对应表单。报告程序 commit/包版本、系统和 CPU/GPU 平台、安装方式、脱敏配置、最小复现步骤、预期和实际结果。图片优先用 [仓库夹具](tests/fixtures/README.md)，不要上传实际用户照片、`.env`、完整数据库、token 或未经审查的日志；无法用夹具复现时先描述条件，不公开业务数据。

画质问题需区分身份变化、错位、遮挡、颜色及生成误差；测试通过、健康就绪或有像素变化不等于成片验收。报告 AI 问题时说明角色别名、调用阶段及公开状态码，不贴上游原始响应和密钥。

## 修改与验证

从 `main`创建独立分支，遵循 [AGENTS.md](AGENTS.md)和相关目录规则。环境、启动及检查命令统一在 [README](README.md#开发与验证)维护；使用 uv/pnpm 和锁文件，不提交模型缓存、素材库、结果、构建产物或实际凭据。

后端集成测试需要按 [模型说明](models/README.md)准备真实模型，在隔离副本运行；外部 AI 使用测试端点/mock，不对真实付费服务做自动回归。前端改动运行格式、类型、测试和构建；纯文档改动运行 [文档治理检查](docs/documentation-governance.md#机械检查)，新增 Wiki 检查见 [发布说明](docs/wiki-publication.md)。

改变行为时同步更新其唯一权威文档、相关回归和 [Changelog](CHANGELOG.md)；有长期取舍时增加 [决策笔记](.agents/notes/README.md)。不要放宽断言、删除失败测试或伪造未执行的目标平台验收。

## Pull Request

说明解决的问题、改动范围、实际运行的命令/结果及未执行项，链接已有 issue。保留维护者和其他贡献者的改动，不夹带无关格式化、业务数据或历史重写。图片或第三方代码须说明来源及使用范围；源码许可状态以 [公开项目说明](docs/public-project.md#源码与资产许可)为准，提交 PR 不替维护者决定顶层许可证。

CI 结果以对应提交的 [Actions](https://github.com/wide-sky-with-me/PersonaWeave/actions)为准。维护者审查后决定是否合并；没有固定响应时限或未发布版本的支持承诺。
