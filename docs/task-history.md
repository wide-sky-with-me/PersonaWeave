# Batch task history and result review

批任务历史是服务端任务状态的只读视图，供查看执行结果与诊断。完整编辑器工程单独保存，且只经显式操作恢复，见 [studio-projects.md](studio-projects.md)。查看历史不替换当前编辑输入。

## Data ownership

**服务端记录：**`api/tasks/persistence.py` 的 `SQLiteTaskStore` 保存可恢复任务元数据和完整快照，是任务事实源。

批任务先等待单个批执行器，再准备参考。详情可返回 `waiting_reason`（等待原因）：内存不足，或当前图片已授权的 AI 轮次。排队不虚构进度；内存等待超时保留已完成制品，仅统计实际尝试，见 [memory-scheduling.md](memory-scheduling.md)。

不可变请求只保存一次，不随每张结果重复序列化。排队 worker（后台执行器）只持任务 ID，执行时加载请求，结束后释放。启动按状态逐步读取，不加载历史请求；显式恢复或来源核验才按需读取一个请求。

**失败如何记录：**

- 新失败结果不内嵌原图，输入仍在持久请求中。带内嵌原图的既有失败行保留在 SQLite，只在详情/恢复时加载，包括无请求快照的行。
- worker 在读取请求或初次持久化时退出，任务进入 `FAILED`，保留已完成项并释放队列，不留下无执行器的 `QUEUED`。回归见 `tests/test_batch_memory_queue.py::test_recipe_load_failure_terminates_dead_queued_task_and_releases_next`。
- 推理返回零可见换脸时，失败行保留真实 `faces_detected`、请求/成功人物序号、`person_outcomes`、相似度和模型路由。状态为 `FAILED`，没有处理图 URL，也不发布图片；只有实际检测为零才记零人脸。流水线出结果前的失败使用 schema 默认值，已丢失的诊断不重建。

**成功图片与浏览器关联：**每个新成功结果在 `outputs/api_results/{task_id}/` 同时保存解码原画布 `original_NNNN.png` 和处理图 `res_NNNN.jpg`。`ImageSwapResult` 只暴露受控 `/api/v1/files/...` URL。缺持久原图副本的旧行沿用 `original_url`；外部地址仍可用时才尽力提供原图对比。

浏览器 `localStorage` 只保存任务与工作区的轻量关联：`taskId`、目标 ID/名称、状态、时间。不存图片字节，也不决定服务端状态。

**重启与可用性：**历史依赖 SQLite 行和引用制品同时保留。文件缺失明确报不可用，不重建替代结果。重启后中断任务为 `FAILED` 并带提示；数量和进度仅按已存结果重算，未处理图片不伪装成失败尝试，已成功图片仍可查看。

## Deletion

`DELETE /api/v1/batch-tasks/{task_id}` 永久删除一个终态任务：`COMPLETED`、`PARTIAL` 或 `FAILED`。

成功返回 `204`，清除 SQLite 快照、内存任务/请求及该任务派生的 `outputs/api_results/{task_id}/`。客户端确认成功后才清除浏览器关联。目录只由服务端任务 ID 和配置输出根派生，不取持久化图片 URL；同步输出与精修输出不受影响。

未知任务返回 `404`。`QUEUED` / `PROCESSING` 返回 `409`，防止后台执行器重新创建已删除状态。

## API read model

列表先取摘要，打开时才取详情：

- `GET /api/v1/task-history/batch-tasks?limit=N`：有上限的轻量 `BatchTaskSummary`，含状态、进度、数量、时间、错误文字和 `viewable_results`。不含 `results`、原图/处理图 URL、请求体、data URI（内嵌图片地址）或其他图片载荷。
- `GET /api/v1/batch-tasks/{task_id}`：用户打开或重连该任务时返回完整 `TaskDetailData`，使列表成本不随图片数增加。
- 完整列表 `GET /api/v1/batch-tasks` 仅供弃用兼容；新代码使用摘要加逐任务详情。

## Frontend behavior

1. 打开任务历史时获取服务端摘要，按 `task_id` 合并浏览器标签。状态以服务端为准；浏览器仅提供目标名称/ID，并证明活动任务属于此工作区。
2. 终态任务可打开只读详情，保留每张已存图片行，包括失败、逐图错误/警告及已有逐人匹配/来源/遮罩诊断；缺诊断显示未知。仅 `SUCCESS` / `PARTIAL` 且有处理图 URL 的结果可查看/下载，前后对比还需原图。失败任务也可能保留已经完成的有效图片。
3. 查看历史不写入 `useStudioStore`。独立的“恢复工程”动作才恢复编辑器；只有显式浏览器关联的活动 `QUEUED/PROCESSING` 任务可重连 Studio 流水线。
4. 任务或制品缺失明确报不可用，不返回空成功或重建替代图。
5. 终态行可经不可逆确认删除，服务端成功后才移除行；活动行不可删除。

**多类型与批量删除：**历史工作区还列出精修会话和旧档案。单条/选择删除都先确认范围；运行中记录不可选，冲突刷新并保留行。记录消失或转为活动时清理选择；删除成功清理详情缓存，部分成功只移除成功项。

列表区分加载、请求失败和空成功。选择控件支持部分选中的混合状态，删除进行中锁定。打开记录或勾选复选框都不触发删除。实现见 [历史工作区](../web/src/features/history/components/HistoryWorkspace.tsx)、[旧档案历史](../web/src/features/history/components/LegacyQualityHistory.tsx)、[选择删除](../web/src/hooks/useRecordDeletion.ts)。

## Complete Studio projects and explicit recovery

编辑器工程保存、恢复与导出统一见 [studio-projects.md](studio-projects.md)。

## Refinement session history

精修会话是独立的服务端记录集合。接口、存储、删除和上限见 [refinement-session-api.md](refinement-session-api.md)，语义见 [ai-refinement.md](ai-refinement.md)。历史按服务端 `limit/offset` 分页，不只在内存中分页最新 50 条；完整版本仅在详情返回。

会话把原图、初稿和参考冻结到自有内容寻址存储，删除原批任务不删除已成功创建的快照。

批结果可记录 `refinement_session_id`，它只作显式导航关联，不授权覆盖 Studio 来源/遮罩或再次生图。本地处理、AI 轮次状态和人工接受分开。打开会话不修改本地工作区；查看版本、选 `base_version_id`（修改基图）和设置 `accepted_version_id`（已接受版）是三个动作。

旧单任务档案 `GET /api/v1/refinement/legacy-tasks` 见 [delivery-quality.md](delivery-quality.md)。旧记录不静默转成会话，也不用于重新启动付费任务。

## Verifiable evidence

- 摘要及详情契约：[`api/schemas.py`](../api/schemas.py)；持久化、制品写入、终态时间和投影：[`api/tasks/batch.py`](../api/tasks/batch.py)、[`api/tasks/persistence.py`](../api/tasks/persistence.py)；路由：[`api/routes/swapping.py`](../api/routes/swapping.py)。
- 合并策略与界面：[`taskHistory.ts`](../web/src/features/history/utils/taskHistory.ts)、[`RecentJobsPopover.tsx`](../web/src/features/history/components/RecentJobsPopover.tsx)、[`TaskResultHistoryDialog.tsx`](../web/src/features/history/components/TaskResultHistoryDialog.tsx)；基于固定测试数据的浏览器检查为 `scripts/smoke_studio_ui.py`。
- 回归：[`tests/test_api.py`](../tests/test_api.py)、[`tests/test_task_restart_persistence.py`](../tests/test_task_restart_persistence.py)、`taskHistory.test.ts`、`RecentJobsPopover.test.tsx`、`HistoryWorkspace.test.tsx`。

设计取舍见 [决策笔记](../.agents/notes/platform/20261004-history-is-a-read-only-projection.md)，仓库规则见 [documentation-governance.md](./documentation-governance.md)。
