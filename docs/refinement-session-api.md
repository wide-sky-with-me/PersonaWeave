# 精修会话接口与存储

本文说明对话式精修的 HTTP 契约、图片存储、接受发布、删除和读取。会话、轮次和版本语义见 [ai-refinement.md](ai-refinement.md)，操作界面见 [refinement-workbench.md](refinement-workbench.md)。可执行 schema（数据校验契约）位于 `api/refinement/contracts.py`。

## HTTP 契约

所有路由都在 `/api/v1/refinement/sessions` 下，根路由不要求结尾斜杠。`revision` 是服务端状态修订号；命令用 `expected_revision` 表明所依据的修订号，避免覆盖新状态。

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

**重复请求与通知：**幂等表示相同 `request_id` 和相同逻辑命令重复提交时复用结果。SSE（服务端事件流）只发送会话 ID、revision 和事件类型，不含模型推理、图片或完整消息。客户端收到通知后取新快照；重连不提交生图。

**失败响应：**载荷冲突或修订号过期为 `409`，图片缺失/损坏为 `410`，命令非法为 `422`。缺模型配置时，只有模型动作返回 `503`；查看已存会话和人工接受不依赖模型配置。

## 存储与按字节发布

证据、候选与发布分开存放：

- SQLite `data/refinement_sessions.sqlite3`：会话、轮次、版本、图片引用、发布记录和事件的事实源。
- 私有证据与候选：`outputs/refinement_staging/sessions/{session_id}/{digest}.png`。digest 是内容摘要；原图、基线和参考在创建时快照并校验摘要，随后保持不可变。
- 已接受文件：`outputs/api_results/refinement/accepted/sessions/{session_id}-{version_id}.png`。只有显式接受才发布到这里。

**接受前校验：**会话归属、`expected_revision`、`version_id`、`candidate_digest` 和字节完整性都必须匹配。有活动轮次、选择过期或冲突时返回错误；对已接受的完好版本重复接受幂等。

**接受后的结果：**发布的是刚校验过的同一字节缓冲，不重新生成、合成或编码。任何保留版本（含旧分支或基线）都可显式接受，无 AI 分数门槛。未接受版本仍可对比和分支；发布不删除会话历史。

**文件与事务：**发布采用私有临时文件加原子重命名。数据库回滚时清理，重启时清理未提交的自有发布文件；私有目录与发布目录必须支持同文件系统原子操作。

[制品 I/O](../api/refinement/artifacts.py)负责文件和完整性，[store](../api/refinement/store.py)负责归属、连续事务与回滚决定。浏览器快照不覆盖服务端事实；图片缺失或损坏时不重建替代图。新会话快照独立于原批任务目录。

**内部快照与外部上传：**无损 PNG 快照采用 `core/image_security.py` 的独立内部预算 `MAX_SNAPSHOT_BYTES`。合法 JPEG 转成无损 PNG 后可能超过外部 20 MiB 上传上限，因此内部快照不套用该字节上限；单图 4000 万像素、内存、累计证据、归属和 SHA-256（内容完整性摘要）检查仍生效。

生图时，网关将内部当前画布按原分辨率无损编码；外部字节/data URI（内嵌图片数据地址）和供应商响应仍受 20 MiB 限制。已知创建输入错误返回具体且脱敏的信息，未知异常只返回脱敏错误。回归见 `tests/test_large_refinement_snapshots.py`。

## 输入与并发上限

一个应用进程负责启动恢复，不是分布式 worker（后台执行器）租约。每个会话最多 200 个轮次，同一时刻最多一个活动轮次。

当前输入共享上限为：24 人、定位文字 300 字符、消息 4000 字符；每人最多 8 张参考，总计最多 64 张。

**累计像素：**`core/refinement/contracts.py` 的 `MAX_EVIDENCE_PIXELS` 限制原图、基线和全部参考的累计解码像素，每次创建最多 1.2 亿（120 million decoded pixels per creation）。重复引用即使快照字节去重，像素仍按每次出现计入。

每张图解码后、写快照前计数。超限则创建失败，只清理本次未登记的快照，不消耗 request ID。执行轮次时，原图、所选基图和参考复用同一预算；没有参考的会话也适用。过大的身份画廊必须由用户显式选图，不静默截断。省略定位时使用原图人脸序号，不取新候选的检测顺序。

**取消与停机：**生图响应进入短暂的快照/登记阶段后，取消会等待该阶段结束，使优雅停机能保留候选。失败或重复创建只清理本次未登记的私有目录。

## 删除

**删除范围：**显式删除空闲会话会移除全部轮次、版本、事件、图片引用及其所有已接受发布。这是记录保留操作；单纯接受不会删历史。原批任务与素材库资产独立，参考是会话冻结副本；文件已缺失也不阻止删除。

**失败响应：**修订号过期或有活动轮次返回 `409`，会话不存在为 `404`，修订号缺失/非法为 `422`，存储失败为 `503`。删除不需要 AI 配置。

**事务顺序：**删除与 SQLite 写入串行。私有目录和发布名只由校验过的服务端 ID 派生；先把自有图片移入私有隔离区，再提交数据库删除。数据库失败时回滚行并恢复文件；符号链接只删除链接本身，不跟随外部目标。

提交后的垃圾清理尽力而为，不承诺跨进程崩溃原子事务或安全擦除。删除在重启后仍成立，旧图片 URL 变为不可用。

事务见 [store](../api/refinement/store.py)，目标枚举与文件处理见 [artifacts](../api/refinement/artifacts.py)、[共享删除 I/O](../api/refinement/deletion.py)。回归见 `tests/test_refinement_sessions.py`、`tests/test_refinement_artifacts.py`。

## 图片读取与缩略图

**完整图读取：**快照创建时先校验完整像素再提交摘要。读取或接受时只核对归属、字节大小和 SHA-256，不完整解码；模型加载证据时仍按像素/内存限制解码。

因此多个版本的缩略图查看不要求逐个分配完整 RGB 画布；解码余量不足不隐藏完好的已存图。篡改或缺失仍返回错误，没有替代图或接受旁路。回归见 `tests/test_refinement_sessions.py::test_history_image_read_and_accept_do_not_decode_full_canvas_under_memory_pressure`。

**缩略图缓存：**版本返回 `thumbnail_url`，人物返回 `reference_thumbnail_urls`。快照写入时生成缩略图，缺缓存时由共享队列补建；缓存有独立摘要，源图仍按流校验摘要与会话归属。缩略图不掩盖源图失效。

`.preview` 是可重建磁盘缓存，随会话目录删除，不进入模型、接受发布或下载；完整图走 `image_url`。前端仅在后端未返回缩略图字段时回退到原图 URL。回归见 `tests/test_refinement_sessions.py`、`tests/test_deferred_evidence.py`、`SessionWorkbench.test.tsx`。

**内存调度：**精修轮次与创建会话的图片冻结共享本地推理内存槽位，整个 AI 轮次持槽。分析/复评逐张加载证据，每人参考板同时只解码一张参考；取消读取线程结束前不释放槽位。

`DeferredImage` 是内部可信的延迟加载描述符，绑定摘要校验过的尺寸，实际读取仍核对摘要与尺寸。排队和内存规则见 [memory-scheduling.md](memory-scheduling.md)。
