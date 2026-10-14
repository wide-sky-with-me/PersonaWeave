# 项目交接文档核对记录

日期：2026-10-14（Asia/Shanghai）

## 范围与基线

任务：提供供下一位维护者使用的综合项目说明，覆盖业务、架构、运行、资产、维护、验证、限制与接任验收。

源码 HEAD：`f0d5fbccaf2df56e3a59928dd65e7031e6aea704`，提交摘要 `fix(windows): guard interrupted updates and publish verified repairs`。

核对对象是该 HEAD 上的**实际工作区**，不是干净提交或发行包。开工时已有 README、Changelog、Dockerfile、Compose、素材库文档修改，及未跟踪的 CPU Compose、容器依赖工具/测试、Docker 部署和 Mac 验收文档、CPU 决策笔记。过程中另有并行素材库部署文档写入；本任务保留这些内容，不回退、不提交或推送。CPU 容器和发布地址配置的工作区事实不表示它们已合入某个发行版。

本任务修改：新增 [项目综合交接说明](../project-handover.md)、[运行维护手册](../operations-runbook.md)与本记录，在 [README](../../README.md)加入入口，在 [Changelog](../../CHANGELOG.md)追加文档变更；没有修改生产实现、运行配置或数据。

## 事实核对

读取项目规则、文档治理及当前专题，并从以下可执行来源核对关键事实：

- 环境、命令及构建：[pyproject](../../pyproject.toml)、[前端清单](../../web/package.json)、[CI](../../.github/workflows/ci.yml)、[dev.py](../../scripts/dev.py)、[Dockerfile](../../Dockerfile)、[Compose](../../docker-compose.yml)、[容器入口](../../docker-entrypoint.sh)。只读取公开 [环境模板](../../.env.example)。
- 入口与对象：[app](../../api/app.py)、[lifecycle](../../api/lifecycle.py)、[schema](../../api/schemas.py)、[会话编排](../../api/refinement/service.py)、[精修契约](../ai-refinement.md)、[前端入口](../../web/src/App.tsx)。
- 数据与恢复：[任务持久化](../../api/tasks/persistence.py)、[会话 store](../../api/refinement/store.py)、[工程 store](../../api/projects/store.py)、[素材库](../../api/portraits/library.py)及领域恢复/重启测试。
- 易误写边界：[文件路由](../../api/routes/files.py)只有目录边界而非登录隔离；[共享槽位](../../api/inference_limiter.py)只接受生产并发 1；[多图准备 Hook](../../web/src/hooks/useIdentityClustering.ts)、[聚类路由](../../api/routes/faces.py)和 [仲裁器](../../core/ai/arbiter.py)证明关闭后置精修不等于零远端调用。
- 生成只传当前单张整图，诊断/复评无自动接受分数门槛；[conversation](../../core/refinement/conversation.py)与 [模型输出契约](../../core/refinement/contracts.py)核对了检索记忆中的历史混杂内容，旧路径/门槛未作为当前能力写入。

文档不手抄接口、模型目录或完整状态机；现行细节链接到所属专题和 schema。源码要求、工作区配置、历史实测与本次验证分别表述。人员、账号归属、备份责任与目标机器验收保持待落实，不填虚构信息。

## 检查结果

- 开工基线：`uv run pytest tests/test_documentation_consistency.py tests/test_documentation_governance.py -q`，23 passed。
- 修改后：`uv run pytest tests/test_documentation_consistency.py tests/test_documentation_governance.py -q`，23 passed；新增文档与 README 均在各自字符上限内。
- 维护手册六个 bash 围栏通过 `bash -n`。在临时合成目录建立四个 SQLite 库及三个普通文件，执行正文备份、独立解包、只读完整性查询：四库均为 ok，七个文件逐字节一致，`.env`未进入归档，查询前后摘要一致。缺备份目录变量、已存在恢复目录均停止；临时目录自动清理。
- `git diff --check`通过；核对 README/Changelog 的实际差异，只追加本次入口和记录，保留原有及并行改动。补充检查三份新增文档的 204 个本地链接/章节锚点与末尾换行、行尾空白，全部通过。

## 验证边界

本次不启动应用，不构建容器/Windows 包，不执行真实 ONNX 或付费模型，不读取实际 `.env`、凭据、业务数据库内容或个人图片。全量 Python/前端回归、目标机器画质验收、真实数据备份/恢复和交接签收未执行；维护手册中的操作流程不是这些项目已通过的声明。

定向文档测试证明长度、分层、链接和受约束的重复事实；临时合成数据检查只证明命令在测试目录按预期工作，不证明用户实例备份完整。接任方仍须按 [交接验收](../project-handover.md#10-接手验收清单)执行目标环境验证。
