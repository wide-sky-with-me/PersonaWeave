# 参与贡献

可以提交可复现的问题、文档修正或范围明确的代码改动。一般使用问题和功能建议走 [Issues](https://github.com/wide-sky-with-me/PersonaWeave/issues)；凭据、个人图片或文件访问暴露走 [私下报告](SECURITY.md)。项目获取、样例和许可见 [公开项目说明](docs/public-project.md)。

## 提交问题

先搜索已有 issue，再选对应表单。一个便于复现的报告应包含：

- **环境**：commit/包版本、系统、CPU/GPU 平台及安装方式。
- **步骤与结果**：最小输入、操作步骤、预期和实际结果。
- **必要证据**：脱敏配置、公开错误状态或经过审查的日志。

图片优先使用 [仓库夹具](tests/fixtures/README.md)。不用真实用户照片、`.env`、完整数据库或 token 作公开复现材料；夹具复现不了时，先描述条件。

画质问题请说明是身份、错位、遮挡、颜色还是生成误差。测试通过、健康就绪或图片发生变化，都不等于成片验收。AI 问题标注角色别名和调用阶段，不贴上游原始响应或密钥。

## 修改与验证

从 `main` 创建独立分支，读 [Agent 规则](AGENTS.md)和相关目录约定。先按 [文档总览](docs/README.md)找到所属专题，再改实现。

启动与检查命令统一在 [README](README.md#开发与验证)维护。使用 uv、pnpm 和锁文件；模型缓存、素材库、结果、构建产物及实际凭据不进入提交。

| 改动类型 | 需要核验什么 |
| --- | --- |
| 后端行为或边界 | Ruff 和对应回归；集成测试在隔离副本准备 [真实模型](models/README.md)，外部 AI 用测试端点/mock |
| 前端 | 格式、类型、测试和生产构建 |
| 纯文档 | [文档治理检查](docs/documentation-governance.md#机械检查)；Wiki 另核对 [发布契约](docs/wiki-publication.md) |

行为改变时，同一变更更新唯一权威文档、相关回归和 [Changelog](CHANGELOG.md)；长期取舍按 [笔记规则](.agents/notes/README.md)记录。不放宽断言或删除失败测试，不把未执行的平台验证写成通过。

## Pull Request

PR 说明要让维护者能独立核对：

- 解决什么问题，关联哪个 issue，改动范围是什么。
- 实际执行了哪些命令，结果是什么；哪些验证尚未执行。
- 图片和第三方代码来自哪里，适用范围是什么。

保留他人改动，不夹带无关格式化、业务数据或历史重写。源码许可状态见 [公开项目说明](docs/public-project.md#源码与资产许可)；提交 PR 不代维护者选择顶层许可证。

持续集成以对应提交的 [Actions](https://github.com/wide-sky-with-me/PersonaWeave/actions)为准。维护者审查后决定合并；项目没有固定响应时限或未发布版本的支持承诺。
