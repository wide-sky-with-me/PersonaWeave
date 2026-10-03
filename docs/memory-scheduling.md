# 多图上传与内存排队

Windows 便携版与 API 使用同一套本地队列。上传 9 张图片不会提高推理并发，也不会为等待中的
本地调用提前解码图片。此优化保持原图分辨率、人物映射和融合计算，不自动缩图或重发付费模型请求。

## 内存生命周期

- 浏览器逐张检测图片尺寸、关闭检测位图，再逐张读取文件；人物聚类的输入比较不拼接整批 base64 字符串。
- `InferenceLimiter` 在图片加载之前获取共享槽位。取消 HTTP 等待者不等于取消原生推理线程，
  该线程实际结束前不会放行下一项。生产仍只允许一个共享流水线推理槽位。
- 同时提交的批任务按提交顺序等待；只有进入执行的批任务才准备参考图。每张目标图的加载、换脸、
  保存都在槽位内完成，返回轻量结果后释放原图与输出数组，再开始下一张。
- 跨图人物聚类逐张检测并保存临时无损 NumPy 快照，只保留尺寸、人脸与特征。需要 VLM 仲裁时
  按需加载参考/目标这一对图片，结束后清理临时目录；人物坐标和 ArcFace 判断仍使用原分辨率证据。
- 批量自动精修先持久化会话关联，再等待本图已提交的 AI 轮次结束，才继续下一张；不会同时启动
  整批图像上下文，也不等待人工接受。等待不会提交或重试新的模型请求。
- 不可变批请求只首次保存，进度更新不重复序列化整批图片。等待中的 worker 仅持有任务 ID，进入执行后才从 SQLite 读取请求，结束后释放；精修溯源和显式工程恢复按需读取，不重新执行任务。启动逐条读取状态，不读取历史请求图片。
- 新失败项不重复保存内联原图，恢复使用持久请求中的输入；旧失败记录的内联原图留在 SQLite，仅详情/恢复时读取，包括没有请求快照的旧记录。摘要和常驻状态不持有这些字节。旧精修档案也不预加载已经不用的请求。
- 工程自动保存成功后，浏览器素材去重缓存仅保留此次保存用到的引用，释放已删除照片和被替换的遮罩。
- 融合按 256 行计算 float32 中间结果，
  最终尺寸及像素与原整幅计算一致。

## 可用内存与等待

`core/memory_budget.py` 不新增依赖。Windows 使用
[GlobalMemoryStatusEx 的物理内存与提交额度](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/ns-sysinfoapi-memorystatusex)
中的较小可用值；Linux 读取 `MemAvailable`，同时考虑可读取的
[cgroup v2 限额](https://docs.kernel.org/admin-guide/cgroup-v2.html)。无法读取时不伪造容量，仍保持串行。

预留 512 MiB 系统余量；解码前按宽×高×4 字节检查，换脸前按已解码 RGB 图像的 20 倍字节数检查
后续工作预算。这些是保守准入预算，不是模型峰值用量预测，也不保证其他程序同时抢占内存时绝不失败。
共享队列仅对这种尚未开始分配/推理的预算不足等待，每秒重新检查，最长 120 秒；普通模型错误不自动重试。
批任务详情的 `waiting_reason` 展示当前原因。内存恢复后继续，超时则停止任务、弹窗说明，保留已经完成的结果，
不把未处理的图片虚记为失败。用户可以先关闭占内存程序；若单张仍无法容纳，再自行选择缩小图片。

## 验证

- `tests/test_memory_budget.py`：Windows 提交额度限制、未知读数、分块/整幅像素一致性。
- `tests/test_inference_limiter.py`：取消后仍占槽、低内存恢复、超时不推理、失败前置检查释放旧帧。
- `tests/test_batch_memory_queue.py`：9 张逐张释放、第二批等待、聚类无损快照按需加载、请求对象执行后回收、重启不预读请求、按需精修溯源。
- `tests/test_task_restart_persistence.py`：旧失败原图按需恢复、摘要不加载图片、无请求快照的原始图片不丢失。
- `tests/test_batch_refinement.py`：精修会话关联先持久化，批流程等待已有轮次而不重复提交。
- `web/src/utils/imageFiles.test.ts`：9 张上传尺寸检测最多同时持有一个位图。
- `uv run python -m scripts.benchmark_compositing_memory`：4000×3000 合成画布实测，融合部分的跟踪分配
  峰值从 432,034,544 字节降至 85,187,952 字节，两者输出 SHA-256 均为
  `66f1c23e7a8fa8c1bf88291d0133589bac712546ad7c2844abbe7e9a8b5af130`。
  此数只表示融合步骤，不是整程序 RAM/显存峰值；原始报告在 `outputs/memory-queue-validation/compositing.json`。

### Windows 原生九图实测

在当前 E 盘 GPU 安装目录，用同一张 3837×2558 原始照片重复构造 9 个不同目标 ID，分别完成真实
跨图分析与本地批量换脸（不是 9 张不同场景的质量评测）。结果为 9/9 成功、0 失败、0 次 AI 调用。
跨图分析 1.83 秒，批流程及结果读取共 97.38 秒；其中确实触发了低内存等待，并自动恢复完成。
例如可用额度约 817 MiB 时未启动下一张需要约 1074 MiB 预算的处理。

包含模型启动在内的原生进程峰值工作集为 1,788,338,176 字节，采样私有提交峰值为
5,634,281,472 字节。两种指标不同，不能用工作集替代提交额度或显存。运行报告、等待状态轨迹及
首尾输出保存在 `outputs/memory-queue-validation/windows-nine-images.json` 和同目录结果图片。
所有 9 张输出 SHA-256 与本轮优化前的同输入单图实测相同：
`f731e5a80c80f21f0f303d060a01ca89befcd72da3359a1c977044e1bb11c9ff`。

## 排队可观测性与线程竞争

`/api/v1/health::local_inference` 只返回本地槽位的等待数量、当前操作类别、阶段和已持续秒数，不保存请求或图片。`waiting_memory` 与 `finishing_cancelled_request` 分别表示内存等待和已取消 HTTP 的原生线程仍在收尾；后者必须真正结束后才能放行。界面每次健康轮询同步这些事实。`model_providers` 从各模型的已加载会话读取，区分 CPU 回退与 CUDA 加载，但不宣称所有算子都在 GPU 执行。

后台读取持久请求或初始化异常不遗留没有 worker 的 QUEUED/PROCESSING 任务：保存 FAILED，保留已有结果并放行后续批次。回归：`tests/test_batch_memory_queue.py`、`tests/test_inference_limiter.py`。

多个串行使用的 ORT 模型通过 `utils/gpu_helper.py::local_session_options` 关闭 `session.intra_op.allow_spinning` 与 `session.inter_op.allow_spinning`，保留 ORT 自动线程数、精度和图优化。依据：[ONNX Runtime 线程管理](https://onnxruntime.ai/docs/performance/tune-performance/threading.html)。Windows i7-12800HX / ORT 1.23.2、同一测试合影仅换一人的三次 CPU 对照：原默认空转为 4.017/4.295/4.604 秒，关闭空转为 约 1.29/1.18/1.12 秒；所有输出像素摘要一致。固定 2/4 线程在本例更慢，因此生产不强行减少线程数。结果不代表另一台机器的耗时。另在 RTX 4070 Laptop 8 GiB 上用相同输入对照 CUDA：热运行约 0.70 秒与 0.71 秒，耗时没有明显改善，但进程 CPU 时间从约 5.1–7.7 秒降到 0.94–1.39 秒；该组输出像素摘要同样一致。关闭空转主要减少无用 CPU 占用，不能承诺解决所有 GPU 慢或内存等待。

可重复证据：[输入/输出摘要与逐阶段计时](validation/local-threads-20260930.json)，诊断脚本 `scripts/benchmark_local_threads.py`（使用安装包 Python，显式给出安装目录、仓库 fixture 目录及输出路径；运行前必须关闭该安装的工作台，避免额外模型占用资源）。只读 `Check-Performance.cmd` 可在排队时运行，读取 RAM、Windows 提交额度、NVIDIA 状态和活动任务，不加载额外模型、不调用 AI。

## 参考图与已保存照片落盘

本地源图逐张提取身份特征后释放解码数组，不将整批参考像素保留到任务结束。显式授权自动精修时，实际读取的参考图先无损保存到任务的 `references/`，用摘要去重；精修通过受控 URL 冻结自己的独立副本，后续素材变化不影响本轮证据。没有自动精修时只保留特征，不额外复制参考图。

工程提交成功且没有并行新编辑、没有本地处理或尚未完成的人脸分析时，将工作区照片的 data URI 替换为工程拥有的磁盘 URL；图片、结果和参考在显示/使用时读取。恢复先逐张校验摘要，再保留 URL，避免并行读取整批照片和将照片全部转回 base64。画笔及撤销帧保留现有 base64 契约。推理模型、当前运算数组与浏览器可见图仍需内存；此变更不承诺固定内存上限，不降低现有准入预算。

精修图片查看不重新解码完整画布，见 [精修契约](ai-refinement.md)。回归：`tests/test_reference_disk_lifetime.py`、`tests/test_batch_refinement.py`、`tests/test_refinement_sessions.py`，以及 `projectController.test.ts`、`largeProjectSnapshot.test.ts`。

## 精修共享队列、缩略图与局部回贴

本地推理、精修会话冻结图片、精修轮次和旧记录缩略图补建共用一个 `InferenceLimiter` 槽位。
精修轮次持有槽位直到分析/生成/复评结束，包括上游等待时间，因此此时本地换脸会排队；这是控制
同时驻留图片的明确取舍，不是显卡卡死。健康状态区分 `refinement_snapshot`、`refinement_turn`、
`refinement_thumbnail`。缓存命中的缩略图、历史列表和原图读取不等待该槽位。AI 调用不通过本地
内存错误重试循环，不会因此增加付费调用。取消图片读取时先等工作线程退出，再释放共享槽位。

精修输入保留摘要校验后的 `DeferredImage` 描述符，阶段需要时才解码；分析/复评逐张编码证据，
参考板逐张读取后仅保留小图块。生成阶段仍给网关传入当前选定版的完整原分辨率数组，候选保存后
释放数组、复评按需重读。总证据像素上限继续按原尺寸计数，不以缩略图规避限制。

局部换脸通过 `core/compositing.py::warp_face_region` 只变换人脸裁剪投影及插值边界；整幅
背景直接复制。融合公式、保护区域、输出尺寸不变。OpenCV 移动目标原点可能产生少量插值取整
差异，不能宣称新旧逐像素完全相同。4000×3000 合成画布的回贴步骤跟踪分配峰值从约
161.4 MiB 降至 44.5 MiB（约 72%）；该次最大单通道差异 1，变化通道占约 0.004%。这不是
整程序内存峰值，也不是最低硬件配置认证。可重复脚本 `scripts/benchmark_region_memory.py`，
报告 [region-compositing-20260930.json](validation/region-compositing-20260930.json)。原生 Windows
真实图片复核另记验证报告；已有全画布融合基准仅证明同一融合公式，不代表新的 ROI 取整完全一致。

回归：`tests/test_compositing_region.py`、`tests/test_deferred_evidence.py`、
`tests/test_refinement_sessions.py`。目前没有 8 GiB RAM/4 GiB VRAM 的受限硬件验收结果，不能把
这些容量列为保证可用的最低配置；16 GiB 机器仍须关注可用提交额度和系统分页文件。

便携启动器给 HTTP 连接设置 10 秒优雅关闭等待，避免浏览器 SSE 订阅长期阻止退出；随后取消剩余 HTTP 等待并执行应用关闭，原生运算仍需完成收尾。这不是推理超时，也不重发 AI 请求。

工程素材导入/保存在图片校验前同样做内存预算。可用内存或 Windows 提交额度不足时，
`/api/v1/projects/*` 返回 HTTP 503 和“等待可用内存”说明，而不是无说明的 500；稍后或释放内存后可重试，
不表示工程数据无效（该路径捕获 `MemoryPressureError`；原生 GPU 冒烟记录：测试机可用量 493 MiB、需约 519 MiB）。实现 `api/routes/projects.py::checked`，回归
`tests/test_projects.py::test_low_host_memory_reports_actionable_503_for_project_media`。
