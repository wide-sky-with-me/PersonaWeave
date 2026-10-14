# 文档与代码组织审查记录

日期：2026-10-14。源码基线：`66b587bf402353c58d9b0f46b9090357da3733f8`（main）；开始时工作区无已有改动。本次仅工作区修改，不提交、推送、发布 Wiki 或修改运行实例。

本记录保留第一轮审查与定向验证的当时结论；维护者追加新分支、完整处理及全量验证要求后的实现和交付见 [分支验收](20261014-organization-branch.md)。

## 范围与证据

读取会话技能目录，加载匹配的 GenUI 报告规范；Ruff 命令按 Context7 返回的 [官方配置文档](https://docs.astral.sh/ruff/configuration/)核对。结构审查使用仓库规则、当前专题、入口注册、导入图和真实 Markdown 链接；独立完成，无团队或其他专家。没有读取实际 .env、业务数据库、个人图片或本机凭据，没有付费 AI 调用。

静态入口图（不是运行实例验收）：API 从 [app](../../api/app.py)注册路由，经 [lifecycle](../../api/lifecycle.py)连接批任务、会话、工程与核心流水线；CLI 入口来自 [pyproject](../../pyproject.toml)与 [api.cli](../../api/cli.py)，导入图包含局部导入及相对导入。UI 从 [main](../../web/src/main.tsx)经 [App](../../web/src/App.tsx)追踪静态/动态导入，类型模块与测试配置单列，不按运行时不可达误删。Python 生产模块均可达，未发现后端/前端运行时导入环。实际 Markdown 链接图未发现现有 docs 或决策笔记孤儿；文件名提及不是这项结论的依据。

Wiki 已发布的状态沿用 [正式发布记录](20261014-github-wiki.md)；本轮未联网重验或再次发布。旧验收、冻结档案和已有决策均未修改。

## 发现（按严重性）

| 等级 | 位置与证据 | 处理 |
| --- | --- | --- |
| 中 | [治理回归](../../tests/test_documentation_governance.py)原用文件名命中判可发现，孤立互链或历史提及也能通过；范围只含 docs 顶层 | 使用当前入口真实链接图，覆盖嵌套 Markdown，反例固定历史出链、仅提及、代码围栏与孤立环 |
| 中 | [包边界回归](../../tests/test_package_boundaries.py)原只看 ImportFrom.module，遗漏 `from .. import app`、`from api import lifecycle` | 解析相对包路径及导入别名，保留单向依赖断言；未发现生产违规导入 |
| 低 | [历史页](../../web/src/features/history/components/HistoryWorkspace.tsx)为缓存键导入工作台 Hook；SSE、删除、接受及缓存清理重复字面值 | 集中到纯 [queryKeys](../../web/src/features/refinement/queryKeys.ts)，原缓存形状与分页失效范围不变 |
| 低 | 文档、公共页及 Wiki 回归重复链接/标题解析，嵌套验收链接缺少统一检查 | 共用非生产 [检查器](../../scripts/documentation_links.py)，保留公开页/Wiki 专项断言 |
| 低 | [架构地图](../../DESIGN.md)遗漏 api/projects，并重复细粒度 AI 配置；[治理来源](../documentation-governance.md)将人物分配归到调用者 pipeline；[精修工作台](../refinement-workbench.md)引用不存在的 useSingleSwap.test.tsx | 补目录职责、配置细节链接权威主题、校准实现来源与真实回归；入口、标题和文件名稳定 |

## 实际修改

功能域内提取查询键，不新增业务包装或反向依赖；共享文档检查不进入生产导入图。现行文档同步治理范围、工程目录与权威链接，Changelog 追加本次记录。导航检查的长期取舍新增 [决策](../../.agents/notes/platform/20261014-rooted-documentation-navigation.md)，不回改旧笔记、不建全局索引、不提高任何长度上限。

## 验证

离线 Python 检查设置 `PYTHON_DOTENV_DISABLED=1`、`LITELLM_LOCAL_MODEL_COST_MAP=True`、`UV_NO_SYNC=1`，避免读取本机环境文件、请求模型目录或同步依赖。基线文档/公开/Wiki/包边界检查：49 passed。首轮 Ruff 检查发现新增导入分组问题，调整后复验干净。

| 检查 | 实际结果 |
| --- | --- |
| 根目录 Ruff format / check | 266 files left unchanged；All checks passed |
| 文档解析、治理、公开页、Wiki、包边界与纯人物分配 | 70 passed；包含相对导入、孤立环、仅历史链接、围栏/行内代码/注释伪链接、重复标题与编码路径反例 |
| 只读证据档案契约 | 1 passed |
| 前端 format / lint | Prettier 与 TypeScript 通过，无额外格式修改 |
| 前端全部 Vitest | 44 个测试文件、246 项通过；新增缓存键形状及分页失效隔离回归 |
| 前端生产构建 | TypeScript + Vite 成功 |
| 差异检查 | git diff --check 干净；既有历史、Wiki 源码、AGENTS、依赖和后端生产源码无改动 |

前端测试日志保留 jsdom Canvas getContext 环境提示及测试预期的后端失败日志，全部断言通过；这不是浏览器真实画布验收。本轮没有全量 API、真实模型、业务数据库或浏览器实测。完成记录后仅复验受影响的文档/公开/Wiki/档案检查：35 passed；新增笔记和本记录人工核对均小于 6,000 字符。

以下命令在仓库根执行，均已运行（代码块仅列复现命令）：

```bash
UV_NO_SYNC=1 uv run ruff format .
UV_NO_SYNC=1 uv run ruff check .
PYTHON_DOTENV_DISABLED=1 LITELLM_LOCAL_MODEL_COST_MAP=True UV_NO_SYNC=1 uv run pytest -q tests/test_documentation_links.py tests/test_documentation_consistency.py tests/test_documentation_governance.py tests/test_wiki_documentation.py tests/test_package_boundaries.py tests/test_public_project_documentation.py tests/test_multi_person_assignment.py
PYTHON_DOTENV_DISABLED=1 LITELLM_LOCAL_MODEL_COST_MAP=True UV_NO_SYNC=1 uv run pytest -q tests/test_delivery_contracts.py
pnpm --dir web format
pnpm --dir web lint
pnpm --dir web test
pnpm --dir web build
git diff --check
```

## 保留的重大结构项

- 不将 api/core 改为 src 布局、不迁移 docs/wiki 子树：安装入口、便携构建器、公开链接及已发布 Wiki 同时依赖现路径。若未来确有收益，先列出完整入链/构建清单，以新路径加原入口转接、分阶段更新所有调用者；直接整体迁移的兼容成本更高。
- [pipeline](../../core/pipeline.py)、[批任务](../../api/tasks/batch.py)和 [会话 store](../../api/refinement/store.py)是较大的可达编排/事务模块。未证明行数导致独立职责冲突；保留连续状态流。若围绕具体变化原因拆分，先固定调用、事务和失败恢复回归，再单独提出纯计算/持久化提取方案；不按行数拆服务或重写算法。
- [工程恢复](../../api/projects/recovery.py)显式依赖 [精修批接续](../../api/refinement/batch.py)的实际人物分配选择，前端工程保存与精修接受也有业务关联；导入图无环。这是跨聚合协作，不足以证明应增加事件总线。可选方案是保留显式编排，或在未来出现多个稳定调用场景时将共享纯规则归到批任务 provenance 并加契约回归，本轮不搬动它。

上述是需独立确认收益和兼容范围的方案，不是已实施或已排期能力。全量 API/真实模型与浏览器实测不作为本次结构整理的默认验收：它们可能初始化持久目录或加载个人运行资产；行为、算法和界面布局本轮没有变更。
