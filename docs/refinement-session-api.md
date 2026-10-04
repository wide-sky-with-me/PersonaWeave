# 精修会话接口与存储

本文定义对话式精修会话的 HTTP 契约、存储、发布、删除和图片读取。会话语义（session/turn/version、证据、模式、状态机）见 [ai-refinement.md](ai-refinement.md)，前端行为见 [refinement-workbench.md](refinement-workbench.md)。可执行 schema 是 `api/refinement/contracts.py`。

## HTTP 契约

所有路由在 `/api/v1/refinement/sessions` 下，根路由不要求结尾斜杠。

| 操作 | 路由 | 边界 |
| --- | --- | --- |
| 创建 | `POST /` | 冻结证据；`request_id` 幂等；不调用模型 |
| 列表 | `GET /?limit=&offset=` | 有界摘要，不含图片、完整对话或请求 |
| 详情 | `GET /{session_id}` | 打开时才返回完整聚合 |
| 删除 | `DELETE /{session_id}?expected_revision=` | 仅空闲且 revision 当前；删除整个会话及其文件 |
| 提交 | `POST /{session_id}/turns` | `request_id`、`expected_revision`、显式基图、模式、消息、可选 `diagnosis_turn_id` |
| 取消 | `POST /{session_id}/cancel` | 显式 revision 检查，不静默切换基图 |
| 接受 | `POST /{session_id}/accept` | 会话归属、revision、所选版本与 digest |
| 图片 | `GET /{session_id}/artifacts/{digest}` | 返回与数据库引用和摘要核对过的同一字节缓冲 |
| 缩略图 | `GET /{session_id}/artifacts/{digest}/thumbnail` | 最长边 384px 的 JPEG |
| 事件 | `GET /{session_id}/events?after=` | SSE，游标或 `Last-Event-ID`，仅轻量快照通知 |

SSE 只发送会话 ID、revision 和事件类型，不含模型推理、图片或完整消息；客户端自己取新快照，重连永远不会提交生图。相同 `request_id` 与相同逻辑命令幂等；载荷冲突或 revision 过期返回 `409`；图片缺失或损坏 `410`；命令非法 `422`；缺少模型配置只阻断模型动作 `503`，不影响查看已存会话和接受。

## 存储与按字节发布

- SQLite `data/refinement_sessions.sqlite3`：会话、轮次、版本、图片引用、发布记录和事件。
- 不可变的私有证据和候选：`outputs/refinement_staging/sessions/{session_id}/{digest}.png`；原图、基线和参考在创建时快照并校验摘要。
- 已接受文件：`outputs/api_results/refinement/accepted/sessions/{session_id}-{version_id}.png`，只有显式接受才发布到这里。

接受校验会话归属、`expected_revision`、`version_id`、`candidate_digest` 和字节完整性；有活动轮次或选择过期/冲突时拒绝；发布已校验的同一缓冲，不重新生成、合成或编码。对已接受的完好版本重复接受幂等。任何保留的版本（含旧分支或基线）都可被显式选为接受版，不存在 AI 分数门槛。接受后未接受的版本仍可对比和分支；发布不抹去会话历史。

发布使用私有临时文件加原子重命名，数据库回滚时清理，重启时清理未提交的自有发布文件；私有目录与发布目录必须支持同文件系统原子操作。浏览器快照不能覆盖服务端事实。图片缺失或损坏返回不可用错误，不重建替代图；新会话快照一经写入就独立于原批任务目录。

服务端的无损 PNG 快照用独立的内部字节预算（`core/image_security.py` 的 `MAX_SNAPSHOT_BYTES`），不套用外部 20 MiB 上传限制（合法 JPEG 无损编码后可能超过它）；单图 4000 万像素、内存检查、累计证据预算、归属和 SHA-256 校验仍然适用。生图时网关对内部当前画布按原分辨率无损编码；外部字节/data URI 和供应商响应仍受 20 MiB 限制。已知的创建输入错误返回具体的安全信息，未知异常保持脱敏。回归 `tests/test_large_refinement_snapshots.py`。

## 输入与并发上限

一个应用进程负责启动恢复，不是分布式 worker 租约。每个会话最多 200 个轮次，同一时刻最多一个活动轮次。当前输入共享上限：24 人、定位文字 300 字符、消息 4000 字符，每人最多 8 张参考、共 64 张。

`core/refinement/contracts.py` 的 `MAX_EVIDENCE_PIXELS` 把原图、基线和全部参考出现的解码像素限制在每次创建 120 million decoded pixels per creation（重复引用即使快照字节去重也计入）。每张图解码后、写快照前计数；超限则拒绝创建并只清理本次未登记的快照，不消耗 request ID。轮次执行对原图、所选基图和参考复用同一预算，没有参考的会话也一样。超大的身份画廊必须显式选择，不静默截断。省略的定位文字取自原图人脸序号，不使用新候选的检测顺序。生图响应进入一个由取消排空的短快照/登记阶段，使优雅停机保留候选；失败或重复的创建只清理自己未登记的私有目录。

## 删除

显式删除会移除空闲会话的全部轮次、版本、事件、图片引用和属于它的所有已接受发布；这是用户对记录保留的决定，单纯接受不会删除历史。revision 过期或有活动轮次返回 `409`，会话不存在 `404`，revision 缺失或非法 `422`，存储失败 `503`；不需要 AI 配置。原批任务和素材库资产保持独立，会话参考是冻结副本；已缺失的文件不阻止删除。

删除与 SQLite 写入串行；私有目录和发布名只由校验过的服务端 ID 派生；先把自有图片移入私有隔离区再提交数据库删除，数据库失败回滚行并恢复文件；符号链接本身被删除而不跟随外部目标。提交后清理私有垃圾尽力而为，不是跨进程崩溃的原子事务，也不是安全擦除。删除在重启后依然成立，旧图片 URL 变为不可用。实现 [store](../api/refinement/store.py)、[共享删除 I/O](../api/refinement/deletion.py)，测试 `tests/test_refinement_sessions.py`。

## 图片读取与缩略图

快照创建时校验完整像素后才提交摘要。读取或接受该摘要时只核对归属、字节大小和 SHA-256，不完整解码；实际给模型加载证据仍按像素/内存限制解码。因此打开多个版本缩略图不要求每个缩略图分配服务端 RGB，解码余量不足也不会隐藏完好的已存图片；被篡改或缺失的图片仍是错误，没有替代图或接受旁路。回归 `tests/test_refinement_sessions.py::test_history_image_read_and_accept_do_not_decode_full_canvas_under_memory_pressure`。

会话版本返回 `thumbnail_url`，人物返回 `reference_thumbnail_urls`。缩略图在快照写入时生成，缺缓存的会话经共享队列补建；缓存有独立摘要，源图按流校验摘要和会话归属，源图失效不能被缩略图掩盖。`.preview` 是可重建的磁盘缓存，随会话目录删除，不进入模型、接受发布或下载；完整图走原 `image_url`。前端只在后端未返回缩略图字段时回退到原图 URL。回归 `tests/test_refinement_sessions.py`、`tests/test_deferred_evidence.py`、`SessionWorkbench.test.tsx`。

精修轮次和建会话的图片冻结共享本地推理内存槽位，整个 AI 轮次持槽；分析/复评逐张加载证据，每人参考板同时只解码一张参考；取消读取线程结束前不释放槽位。排队与内存规则见 [memory-scheduling.md](memory-scheduling.md)。`DeferredImage` 是内部可信的加载描述符，绑定摘要校验过的尺寸，实际读取仍核对摘要和尺寸。
