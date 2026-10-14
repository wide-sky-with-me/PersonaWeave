# 多图上传与内存排队

**结论与边界。** Windows 便携版和 API 共用一个本地推理槽位。上传 9 张图不提高并发，等待中的本地调用不提前解码；保留原分辨率、人物映射和融合公式，不自动缩图或重发付费请求。内存预算是准入检查，不是峰值预测或最低硬件认证。

## 内存生命周期

- **浏览器输入：** 逐张检查尺寸、关闭检测位图后再逐张读文件；聚类比较不拼接整批 base64。
- **本地执行：** `InferenceLimiter` 在加载图片前获取共享槽位。HTTP 等待取消不等于原生线程取消，线程实际结束才放行；生产只有一个共享流水线槽位。
- **批任务：** 按提交顺序等待，仅进入执行时准备参考图。每张目标的加载/换脸/保存均在槽位内，返回轻量结果、释放原图/输出数组后处理下一张。
- **跨图聚类：** 逐张检测，临时无损 NumPy 快照落盘，仅常驻尺寸、人脸及特征。VLM 仲裁按需读参考/目标这一对，结束清临时目录；人物坐标与 ArcFace 判断用原分辨率证据。
- **自动精修：** 先持久化会话关联，等本图已提交 AI 轮次结束再继续下一张，不同时启动整批图片上下文，也不等人工接受；等待不提交或重试模型请求。
- **请求持久化：** 不可变批请求只首次保存，进度不重序列化图片。等待 worker 只持任务 ID，执行时才从 SQLite 读请求、结束释放；精修溯源/显式工程恢复按需读，不重跑任务。启动逐条读状态，不读历史请求图片。
- **失败记录：** 新失败项不重复存内联原图，恢复取持久请求。兼容失败记录的内联原图仍留 SQLite，包括无请求快照的记录，仅详情/恢复时读；摘要/常驻状态不持字节。兼容精修档案不预加载无须使用的请求。
- **工程缓存：** 自动保存成功后，浏览器去重缓存只留本次用到的引用，释放删除照片/替换遮罩。
- **融合：** 256 行分块计算 float32 中间结果；与同一融合范围的整幅公式尺寸和像素一致，局部 warp 的取整限制另见末节。

## 可用内存与等待

**读数。** `core/memory_budget.py` 不新增依赖。Windows 取 [GlobalMemoryStatusEx](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/ns-sysinfoapi-memorystatusex) 的可用物理内存与剩余提交额度较小值；提交额度是系统允许进程承诺使用的内存，不等于 RAM 或显存。Linux 读 `MemAvailable`，并考虑可读的 [cgroup v2 限额](https://docs.kernel.org/admin-guide/cgroup-v2.html)。读数未知时不虚构容量，仍串行。

**准入预算。** 预留 512 MiB 系统余量；解码前检查宽×高×4 字节，换脸前按已解码 RGB 图像字节数的 20 倍检查后续预算。它们是保守检查，不是模型峰值预测，也不保证其他程序抢占内存时绝不失败。

**等待与结果。** 只对尚未开始分配/推理的预算不足等待，每秒重查，最长 120 秒；普通模型错误不自动重试。批详情的 `waiting_reason` 显示原因。内存恢复则继续，超时停止任务并弹窗，保留完成结果，不把未处理图片虚记为失败。先关闭占内存程序；单张仍放不下时，用户自行选择缩小图片。

## 验证

以下回归分别检查生命周期和准入，不等于真实硬件验收：

- `tests/test_memory_budget.py`：Windows 提交限额、未知读数、分块/整幅像素一致。
- `tests/test_inference_limiter.py`：取消后占槽、低内存恢复、超时不推理、失败前检查释放旧帧。
- `tests/test_batch_memory_queue.py`：9 张逐张释放、第二批等待、聚类快照按需读、执行后回收请求、重启不预读、按需精修溯源。
- `tests/test_task_restart_persistence.py`：兼容失败原图按需恢复、摘要不加载图片、无请求快照的原图保留。
- `tests/test_batch_refinement.py`：先持久化关联，等已有轮次、不重复提交。
- `web/src/utils/imageFiles.test.ts`：9 张上传的尺寸检测同时最多一个位图。

**融合基准。** `uv run python -m scripts.benchmark_compositing_memory` 的 4000×3000 合成画布，融合跟踪分配峰值为整幅 432,034,544 字节、分块 85,187,952 字节，两输出 SHA-256 均为 `66f1c23e7a8fa8c1bf88291d0133589bac712546ad7c2844abbe7e9a8b5af130`。仅测融合步骤，不是全程序 RAM/显存峰值；报告 `outputs/memory-queue-validation/compositing.json`。

### Windows 原生九图实测

**样本范围。** E 盘 GPU 安装目录的实测把同一张 3837×2558 原始照片构造成 9 个不同目标 ID，完成跨图分析与本地批量换脸，不是 9 个不同场景的质量评测。

**结果。** 9/9 成功、0 失败、0 次 AI 调用；跨图分析 1.83 秒，批流程及结果读取共 97.38 秒。触发低内存等待后自动恢复，例如可用约 817 MiB 时没有启动下一张需约 1074 MiB 预算的处理。

**指标边界。** 含模型启动的原生进程峰值工作集为 1,788,338,176 字节，采样私有提交峰值为 5,634,281,472 字节。工作集表示驻留物理内存，不能替代提交额度或显存。运行报告/等待轨迹在 `outputs/memory-queue-validation/windows-nine-images.json`，首尾结果在同目录。

9 张输出均与对照同输入单图实测的 SHA-256 一致：`f731e5a80c80f21f0f303d060a01ca89befcd72da3359a1c977044e1bb11c9ff`。

## 排队可观测性与线程竞争

**看队列。** `/api/v1/health::local_inference` 只返回等待数、当前操作/阶段/已持续秒数，不存请求或图片；界面每次健康轮询同步。`waiting_memory` 是内存等待，`finishing_cancelled_request` 是 HTTP 已取消而原生线程仍收尾，结束才放行。`model_providers` 来自加载会话，区分 CPU 回退/CUDA 加载，不证明所有算子都在 GPU。

持久请求读取或后台初始化失败会存 FAILED、保留结果并放行后续批次，不留下无 worker 的 QUEUED/PROCESSING。回归：`tests/test_batch_memory_queue.py`、`tests/test_inference_limiter.py`。

**线程策略。** `utils/gpu_helper.py::local_session_options` 关闭串行 ORT 模型的 `session.intra_op.allow_spinning`、`session.inter_op.allow_spinning`（空闲线程自旋），保留自动线程数、精度和图优化。依据：[ORT 线程管理](https://onnxruntime.ai/docs/performance/tune-performance/threading.html)。限定对照：

- **CPU：** Windows i7-12800HX / ORT 1.23.2，同一合影只换一人，各三次。默认自旋 4.017/4.295/4.604 秒；关闭自旋约 1.29/1.18/1.12 秒，输出像素摘要一致。固定 2/4 线程在此更慢，因此生产不强减线程数；不外推到其他机器。
- **CUDA：** RTX 4070 Laptop 8 GiB，同输入热运行约 0.70 与 0.71 秒，无明显耗时改善；进程 CPU 时间由约 5.1–7.7 秒到 0.94–1.39 秒，输出像素摘要一致。主要减少无用 CPU 占用，不承诺解决全部 GPU 慢或内存等待。

证据：[输入/输出摘要与分阶段计时](validation/local-threads-20260930.json)。`scripts/benchmark_local_threads.py` 使用安装包 Python，须显式给安装目录、仓库 fixture 目录和输出路径，运行前关闭该安装的工作台，避免额外模型占用。排队时可运行只读 `Check-Performance.cmd` 查看 RAM、Windows 提交额度、NVIDIA 状态及任务，不另加载模型或调用 AI。

## 参考图与已保存照片落盘

**参考图。** 源图逐张提取身份特征后释放解码数组，不常驻整批像素。显式授权自动精修时，实际读取参考无损保存到任务 `references/`、按摘要去重；精修经受控 URL 冻结独立副本，后续素材变化不影响证据。无自动精修只留特征，不额外复制。

**工程照片。** 提交成功、无并行新编辑/本地处理/未完成脸分析时，将工作区照片 data URI 换成工程拥有的磁盘 URL，显示/使用时读。恢复逐张校验摘要后保留 URL，不并行读整批或全部转 base64；画笔/撤销帧保持 base64 契约。模型、当前运算数组及可见图仍需内存，不承诺固定上限，也不降准入预算。

精修看图不重新解码完整画布，见 [精修契约](ai-refinement.md)。回归：`tests/test_reference_disk_lifetime.py`、`tests/test_batch_refinement.py`、`tests/test_refinement_sessions.py`、`projectController.test.ts`、`largeProjectSnapshot.test.ts`。

## 精修共享队列、缩略图与局部回贴

**共享槽位。** 本地推理、精修冻结、精修轮次、兼容记录缩略图补建共用一个 `InferenceLimiter`。精修轮次从分析到生成/复评结束均占槽，包括上游等待，所以本地换脸会排队，不是显卡卡死。健康区分 `refinement_snapshot`、`refinement_turn`、`refinement_thumbnail`；缓存缩略图、历史列表、原图读取不等槽。AI 不走本地内存错误重试，不增加付费调用；取消图片读取先等线程退出再放槽。

**按需解码。** 摘要校验后的 `DeferredImage`（延迟解码描述符）只在阶段需要时解码。分析/复评逐张编码，参考板逐张读取后仅留小块；生成仍输入选定版的完整原分辨率数组，候选保存后释放、复评按需重读。证据像素上限按原尺寸计，不用缩略图规避。

**局部回贴。** `core/compositing.py::warp_face_region` 只变换脸裁剪投影及插值外缘，背景直接复制；融合公式、保护与输出尺寸不变。OpenCV 移动原点有少量取整差异，不宣称与全画布 warp 逐像素相同。

4000×3000 合成画布的回贴跟踪分配峰值：整幅约 161.4 MiB、局部 44.5 MiB，减少约 72%；最大单通道差异 1，变化通道约 0.004%。不是全程序峰值或最低配置认证。脚本 `scripts/benchmark_region_memory.py`，报告 [region-compositing-20260930.json](validation/region-compositing-20260930.json)。原生 Windows 真图复核另记验证报告；全画布融合基准只证明相同融合公式，不证明 ROI（局部区域）warp 取整一致。

回归：`tests/test_compositing_region.py`、`tests/test_deferred_evidence.py`、`tests/test_refinement_sessions.py`。没有 8 GiB RAM/4 GiB VRAM 受限硬件验收，不能保证它们是最低配置；16 GiB 机器仍须关注可用提交额度与分页文件。

**关闭与工程排错。** 便携启动器给 HTTP 连接 10 秒优雅关闭等待，避免 SSE 订阅长期挡住退出；随后取消剩余 HTTP 等待并关闭应用，原生运算仍需收尾。这不是推理超时，不重发 AI。

工程素材导入/保存在图片校验前也检查内存。内存/Windows 提交额度不足时，`/api/v1/projects/*` 返回 HTTP 503 和“等待可用内存”，不是无说明的 500，也不表示工程无效；释放内存后或稍后可重试。实现 `api/routes/projects.py::checked` 捕获 `MemoryPressureError`；原生 GPU 冒烟记录为可用 493 MiB、需约 519 MiB。回归：`tests/test_projects.py::test_low_host_memory_reports_actionable_503_for_project_media`。
