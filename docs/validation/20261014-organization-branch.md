# 组织结构优化分支验收

日期：2026-10-14（Asia/Shanghai）。分支：`refactor/organization-and-boundaries`；基线：`66b587bf402353c58d9b0f46b9090357da3733f8`。本记录覆盖 [第一轮组织审查](20261014-organization-audit.md)后的完整收尾，原记录保留其当时范围。

## 逐项处理

| 审查项 | 分支处理与证据 |
| --- | --- |
| 文档真实可发现性、共享链接解析 | 保留入口链接图与反例回归，覆盖嵌套记录、仅提及、孤立环和伪链接；[文档检查器](../../scripts/documentation_links.py)、[检查回归](../../tests/test_documentation_links.py) |
| 依赖方向与前端查询键 | 相对导入/别名护栏及统一查询键；[包边界](../../tests/test_package_boundaries.py)、[queryKeys](../../web/src/features/refinement/queryKeys.ts) |
| 流水线与批任务职责 | [单脸执行](../../core/face_execution.py)持有对齐、掩码及回贴；[pipeline](../../core/pipeline.py)保留模型/锁与连续人物编排；任务来源和已分配结果在现有 [provenance](../../api/tasks/provenance.py)共享 |
| 会话事务与文件副作用 | [artifacts](../../api/refinement/artifacts.py)提取快照/摘要/预览/文件发布及清理；[store](../../api/refinement/store.py)保留归属、SQLite、revision、事件和连续接受/取消/删除/恢复事务 |
| 工程恢复跨聚合依赖 | [recovery](../../api/projects/recovery.py)直接使用任务来源规则，不导入精修接续编排；保留实际角色、画布与原任务关联 |
| 顶层目录/大文件重组 | 保留 api/core/utils、docs/wiki 与受支持入口；按计算、任务来源及 I/O 的真实边界拆分，不按行数拆状态机或增设 src 转发树。安装及公开路径兼容由护栏与实际构建检查，取舍见 [决策](../../.agents/notes/platform/20261014-domain-extraction-with-stable-entrypoints.md) |

## 验证范围

前端在只含版本化源码的临时副本执行，依赖目录独立复制，依次运行 `pnpm format:check`、`pnpm lint`、`pnpm test`、`pnpm build`：44 个测试文件、246 项通过，类型/格式与生产构建通过。完成后逐文件摘要确认原前端源码未变；临时依赖、缓存和构建产物清理。jsdom 缺少 Canvas getContext 的环境提示及测试预期的失败日志不代表真实浏览器画布验收。

后端把 `git ls-files --cached --others --exclude-standard` 中源码复制到临时目录，复用现有依赖环境但不 sync；断言 api/core/utils 均来自隔离副本。只挂载六个现有 ONNX 资产，经 `uv run --no-sync python scripts/download_models.py` 完整性校验后运行 `uv run --no-sync pytest -q`：**964 passed in 53.55s**，无失败或跳过。数据库、输出及测试状态均留在副本；禁用 dotenv、启用本地费用表，AI_VISION/AI_IMAGE 使用显式测试模型、测试密钥和 `.test` 地址；没有真实 AI 调用。

安装验证：隔离副本 `uv build --wheel --out-dir <临时目录>` 成功，解包验证七个包/领域模块来自 wheel；再以 `uv pip install --offline --no-deps --target <临时安装目录>` 实际安装本地 wheel，验证模块路径和 distribution entry_points，并通过生成的 `personaweave --help`、`personaweave-import-portraits --help` 两个命令冒烟。安装复用既有第三方依赖，不是全新平台依赖安装或 Windows 原生交付验收。

全仓 `uv run ruff format .` 为 273 files left unchanged，`uv run ruff check .` 与 `git diff --check` 通过。全量结束后逐文件 SHA-256 确认原工作区源码没有被测试改写；临时数据库、模型硬链接、输出、wheel 和副本全部清理。最后仅补本记录与保护画笔规则中的实现路径，再跑文档治理回归，不重复业务测试。真实 AI、GPU 画质、浏览器和 Mac 部署验收未执行；通过不能扩展为这些能力声明。

## 发布与部署边界

只提交并推送本分支，不合并 main，不重新发布 Wiki，不执行 SSH/Colima/Docker/LaunchAgent 或实例升级命令。Mac 上部署的容器、守卫、AI 连接、代理规则、素材库、结果和持久目录保持不变。源码通过与运行实例升级是两件事；最终远端 commit 与 CI 单独核对。
