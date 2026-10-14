# Studio projects and explicit recovery

**编辑器工程**保存可修改的 Studio 工作上下文；[任务结果历史](task-history.md)保存只读执行记录，二者用途不同。工程文档存于 `data/projects.sqlite3`，独立图片按内容摘要存于 `outputs/api_results/projects/{project_id}/`。

一个工程是显式关联的任务/会话共享的最新已保存编辑状态，不改写执行配方。多个任务/会话可关联同一工程；每条记录保留首次所属工程，后续保存不把其他工程的记录重新归属。

## Saving

**保存内容：**编辑暂停后自动保存。界面区分“有未保存修改”“正在保存”“已保存到服务器”和“工程保存/恢复失败”，这些是操作提示。工程包括：

- 目标图与结果、来源人物/参考、原图冻结的人脸拓扑和映射，以及全部可编辑参数。
- 每图保护遮罩与撤销/重做记录、活动目标、画笔和视图设置。
- 精修会话关联、查看/基图版本、分析确认、模型/对比/缩放/分栏选择及未提交意见。

受管理的人像原图仍是处理参考；显示缩略图另外按原字节归档。不透明 ID（不暴露磁盘路径的标识）经 SQLite 解析，并校验文件在受管理目录内；因此恢复不依赖原素材库。执行参考冻结为手动参考，人物 ID 和标签保持稳定。实现见 `api/projects/recovery.py`，回归见 `tests/test_projects.py`。

**提交顺序与失败：**所有必需图片上传成功后才提交文档。保存失败保留上一修订版并显示错误，不把未保存编辑称为已持久化。JSON 文档用 digest（内容摘要）引用图片；上传校验既有字节/像素上限，在工程内去重并保留编码字节。

文档上限为 4 MiB，不含图片字节。不支持的 schema（结构版本）、临时 blob/data 图片地址、原型键和过深嵌套都会校验失败。

**多标签页冲突：**保存按 revision（修订号）做乐观并发校验，并串行上传媒体。旧编辑器收到冲突，不覆盖另一标签页；“另存为新工程”可将冲突中的编辑器保存为独立工程。

关闭或重载时若保存未完成，使用浏览器未保存更改提示。没有到达服务端的修改不具备崩溃恢复保证。保存成功后清理未用的图片/遮罩去重键；切换工程会清空缓存。

**大快照：**服务端编码的 PNG 使用有界内部预算，外部上传仍限 20 MiB，像素和内存检查均保留。恢复时，超过上传上限的资产先校验摘要，再以受控 `/api/v1/files/projects/{project_id}/{digest}` URL 使用，自动保存不重复作为超大上传提交。

这些独立副本在会话删除后仍有效；只有会话发布 URL 不等于独立副本。回归见 `tests/test_large_refinement_snapshots.py`、`web/src/features/projects/largeProjectSnapshot.test.ts`。

## Restoring

“查看”或打开会话只看历史。**“恢复工程”**才会先保存当前编辑器，恢复完整已存工程并回到编辑；本地执行进行中时阻止切换工程。“新工程”也先保存当前编辑器，再清空输入。

**恢复顺序：**先获取并逐一校验所有图片的 SHA-256（完整性摘要），全部通过后才原子更新编辑器状态。任何图片缺失或损坏都让编辑器保持原样。

HTTP IP 部署即使没有 `crypto.subtle`，也按同一编码字节和 SHA-256 契约校验。见 [浏览器工具](../web/src/utils/browserCrypto.ts)、[回归](../web/src/features/projects/largeProjectSnapshot.test.ts)、[决策](../.agents/notes/frontend/20261014-verify-project-assets-without-secure-context.md)。

**浏览器与服务端各管什么：**浏览器本地工程元数据只有当前不透明工程 ID。刷新加载该工程；另一浏览器可从历史选择它。精修轻量选择和待提交命令还可通过 `sessionStorage` 保留同标签页刷新状态，但恢复不重放待提交命令。

运行标志、活动任务 ID、回调、服务端进度和接受指针不从工程文档恢复，任务/会话服务仍是权威。恢复不启动换脸、不发送 AI 轮次、不接受候选，也不重新检测/排列已存人脸。中断的 `PROCESSING` 预览恢复为空闲；活动任务重连仍需精确的浏览器-目标关联。

确认批任务缺失（404/410）时，关联的处理中图片标为失败，重试/编辑解锁。新建或恢复工程会清理 App 级批任务摘要、详情和错误缓存。回归见 `useBatchPipeline.test.tsx`、`projectController.test.ts`。

## Endpoints

以下是操作入口；数据契约以实现和 schema 为准：

- `GET /api/v1/projects?limit=`：有界摘要（`id/title/revision/updated_at`），无文档或图片。
- `POST /api/v1/projects`：创建上传目标；未保存的空行不进历史。
- `GET /api/v1/projects/{id}`：读取一个完整已存文档，只读。
- `POST /api/v1/projects/{id}/restore`：打开已存编辑器，并从关联批记录补齐缺失精修输入。列表恢复与页面刷新使用它。
- `PUT /api/v1/projects/{id}`：修订号校验的文档保存与显式任务/会话关联。
- `POST /api/v1/projects/{id}/assets`：冻结图片或原始人像资产/身份选图，不调用模型。
- `GET /api/v1/projects/{id}/assets/{digest}`：读取归属及摘要校验后的字节。
- `POST /api/v1/projects/recover/{batch|session}/{record_id}`：打开所属工程，或将可用旧记录显式迁入新的部分工程。
- `DELETE /api/v1/projects/{id}?expected_revision=`：只删该工程文档、关联和自有副本。任务结果、精修版本、素材库保持独立。

删除任务/会话历史不删除独立保存的工程图片；但编辑器快照不能重建已删除会话的轮次和接受。主机内存不足时，工程媒体导入返回 `503` 及“等待可用内存”，而非无说明的 500，见 [memory-scheduling.md](memory-scheduling.md)。

## Records without an editor snapshot

**可恢复的范围：**旧批请求可保留参数、映射、参考和最终遮罩。恢复复用实际字段、持久原图/结果和既有精修关联。旧会话可提供原图、基线和各张参考图，按保存的人物/人脸序号绑定填充部分工作区。

参考板拼图不替代原始人像参考。缺失人脸序号不猜测；迁移不跑检测器或付费请求。

**没有会话的批结果：**用已存请求的内容寻址参考，以及该图实际 `person_outcomes`（人物执行结果）的人脸分配，重建缺失精修输入。其他图片未分配的跳过角色不带入，也不阻止本图已确认人物。

原图和本地结果必须与编辑器画布一致：资产摘要相同可直接通过；编码不同时须解码像素完全相等。工程列表、批/会话历史、页面刷新都使用这一修复入口，保留已有配方、编辑与草稿。

单来源请求未指定人脸序号时，只用实际分配，不推断编号。分配/参考缺失，或执行时未冻结身份素材成员关系，就保持不可用；当前素材库选图不替代执行证据。删除批记录不阻止独立工程使用。回归见 `tests/test_projects.py`、`tests/test_batch_refinement.py`。

**运行中批任务：**结果到达前保存 `pendingRefinement`，即任务 ID 加冻结参考输入。仅当活动任务显式关联且 ID 匹配，重连才用不可变原图/结果 URL 完成配方；不取当前选图、不调用模型，也不独立重连或执行任务。尚无会话的精修目标切换也自动保存。

待接续输入须在崩溃/刷新前到达服务端，才具备已存状态保证。回归见 `BatchControls.test.tsx`、`projectController.test.ts`、`projectSnapshot.test.ts`。

**接受结果缺少会话关联：**显式恢复仅在恰有一个已接受会话匹配时补关联，须同时匹配目标 ID、规范化原图/本地画布摘要及全部身份参考绑定（人物 ID、人脸序号、参考摘要）。无参考旁观人物不增加身份绑定。

已有关联不重新分配；画布或参考变化不匹配，多个匹配会话则保持未解决。此路径不接受版本、不创建轮次，见 `api/refinement/store.py` 的 `matching_accepted_sessions`、`api/projects/recovery.py` 的 `restore_accepted_session_links`。

**缺失项如何处理：**不可用文件、缺原始请求、人脸框、未存意见、原 UI 名称和遮罩撤销/重做历史，不凭空补回。工程列出缺失项：缺框保留为空集，缺参数用当前 UI 默认值并提示；可用媒体仍可编辑，AI 会话仍可打开。旧单任务档案沿用只读/人工接受边界，见 [delivery-quality.md](delivery-quality.md)。

## Final results and export

已接受结果如何进入工作区、单图/ZIP 如何校验、删除后的处理，统一见 [refinement-workbench.md](refinement-workbench.md)。

ZIP 保留下载字节，以检测到的 PNG/JPEG/WebP 格式选扩展名，并保证文件名唯一。导出从当前编辑器收集已完成目标，没有活动批任务也可用。任务详情仍是不可变本地执行记录，不等于编辑器最终导出，实现见 `web/src/utils/workspaceExport.ts`。

## Evidence

实现见 [`api/projects/store.py`](../api/projects/store.py)、[`api/projects/recovery.py`](../api/projects/recovery.py)、[`api/routes/projects.py`](../api/routes/projects.py)、[`projectController.ts`](../web/src/features/projects/projectController.ts)、[`projectSnapshot.ts`](../web/src/features/projects/projectSnapshot.ts)；输入契约见 [`api/schemas.py`](../api/schemas.py)。

回归见 [`test_projects.py`](../tests/test_projects.py)、`projectSnapshot.test.ts`、`projectController.test.ts`。非生产浏览器检查脚本为 `scripts/smoke_project_recovery.py`。
