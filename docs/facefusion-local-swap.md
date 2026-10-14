# FaceFusion local swap parity

本地生产换脸采用 FaceFusion 提交 `4b1dedb853e4838ca7f3cf70b572be241aee2497` 的处理序列（https://github.com/facefusion/facefusion）。默认是本地身份迁移，不是自然度/画质验收；支持的操作与模型许可边界见下文。

融合以 256 行 float32 分块计算，与同一融合范围的整幅公式逐像素一致；局部仿射回贴的取整限制见「回贴内存范围」。上传解码、本地推理和批处理共用有界队列及系统内存检查，见 [内存调度](memory-scheduling.md)。内存生命周期不改变分辨率、身份匹配、掩码语义或融合公式。

**处理顺序。** 对齐裁剪指将人脸变换到模型模板空间；掩码决定允许回贴的像素，交集保留每种保护限制。

1. 按模型模板做标准五点对齐。仿射实际缩小时用 `INTER_AREA`，pixel boost（像素增强）放大时用 `INTER_CUBIC`；放大用 `INTER_AREA` 会接近 `INTER_NEAREST`。
2. 对大于模型原生输入的人脸执行 FaceFusion 风格 pixel boost。
3. 对齐小块分别推理，按交错布局精确重组。
4. 取柔化框、106 点解剖、XSeg 遮挡和 BiSeNet 语义保护掩码的交集。关键点提取异常时，在 512×512 空间生成默认椭圆并做 31×31 高斯羽化，再缩到裁剪尺寸参与交集；诊断保留失败，不冒充实测关键点。XSeg/BiSeNet 羽化随裁剪宽度缩放：256px 基准 sigma=5，BiSeNet 在 128px 为 2.5。BiSeNet（`core/region_parser.py`、`models/bisenet_resnet_34.onnx`、19 类 CelebAMask-HQ）默认保护 `glasses`，包含镜片，不为抬高身份分数移除保护。
5. 只在掩码边界环做色彩校正：计算换脸裁剪与未换脸对齐裁剪间的 Reinhard LAB 色彩协调，仅将窄边缘环混入结果，不改人脸内部色调，用于减轻曝光/白平衡接缝。`color_transfer_mode="off"` 时关闭。
6. 逆仿射 alpha（透明度）回贴，最后扣除手动保护。

**实现边界。** `core/swapper.py` 适配器只暴露 `infer_aligned()`；`PersonaWeavePipeline.swap_single_face_facefusion()` 把对齐、掩码、校正和回贴交给 [`face_execution.py`](../core/face_execution.py)，没有第二套适配器回贴路径，模板保持归一化。回归：`tests/test_facefusion_pipeline.py`、`tests/test_swap_diagnostics.py`。

生产不支持 Poisson（泊松）克隆、全脸色彩迁移或启发式头发保护；它们可能改变结构、身份或保护像素。`swap_single_face_seamless()` 不是备用入口。Ghost 仅用于显式实验；自动路由用 InSwapper，除非用户启用满足条件的 HyperSwap 路径。

**可选修复。** `enable_face_restoration` 默认关闭。`core/restorer.py` 的 `CodeFormerRestorer` 修复 256×256 工作裁剪上限损失的细节，输出只通过换脸所用的 `crop_mask`（关键点/遮挡/语义保护交集）混合，不扩展到背景、遮挡或保护像素。`restoration_fidelity_weight` 使用 CodeFormer 身份控制偏向换脸后的身份；GFPGAN 无此控制，不属于支持路径。

## Execution providers

**结论。** Provider 是 ONNX Runtime 执行后端。健康接口报告模型会话实际加载的 Provider，不是安装包声明的能力；算子是否在 GPU 执行仍须 profiling（执行记录），见 [排队与线程竞争](memory-scheduling.md#排队可观测性与线程竞争)。

生产模型构造均用 `utils/gpu_helper.py::local_session_options` 关闭 ORT intra/inter-op 空闲自旋，避免六个串行模型线程池竞争；线程数与推理数学不变。Windows CPU/GPU 版共用流水线：检查系统 NVIDIA 驱动/设备数，预加载包内 CUDA/cuDNN DLL 并检查 CUDA Provider 依赖链，缺驱动或库选 CPU；Linux 保留共享库预加载与驱动检查。Windows GPU 验证须记录六个随包 ONNX 模型的真实执行，`get_available_providers()` 不足以证明。要求与限定实测见 [便携版](windows-portable.md)、[便携版验证](windows-portable-validation.md)。

## BiSeNet model provenance

**来源与摘要。** `models/bisenet_resnet_34.onnx` 属于 FaceFusion 模型集（vendor yakhyo，MIT，Hugging Face `facefusion/models-3.0.0`），在默认下载集合中。SHA-256：`4a0b8c958a3c938913bd06a8365dbb3c8761afba6ecbf0d14b3b1f77eb230c96`。同名 15 字节 `Entry not found` 是失败下载的文本占位，不是权重。

**接口证据。** FaceFusion 源码及 `tests/fixtures/source-01.png` 的真实推理核对：输入 `input` 为 512×512，BGR→RGB、ImageNet 归一化、NCHW（批次/通道/高/宽）；输出 `[0][0]` 为 `(19, 512, 512)` logit（类别响应）图。CelebAMask-HQ 类别顺序：

- 0 background，1 skin，2/3 eyebrows，4/5 eyes，6 glasses，7/8 ears，9 earring。
- 10 nose，11 mouth，12/13 lips，14 neck，15 necklace，16 cloth，17 hair，18 hat。

## Multi-person mapping assignment

**人物分配。** `person_mappings` 由 [`resolve_person_mapping_assignment()`](../core/face_assignment.py) 在完整余弦矩阵上调用 `scipy.optimize.linear_sum_assignment`（匈牙利算法），求全局最优一对一映射，调用方为 `core/pipeline.py`。低于阈值的配对在优化前屏蔽、优化后再按真实相似度校验，行/列竞争不强迫无效匹配。

**跨图聚类是另一条规则。** `api/routes/faces.py` 可用 VLM 建议把孤立人脸合入簇；`VLMIdentityArbiter.refine_clusters()` 仍须复查仲裁参考与孤立人脸的 ArcFace 余弦是否达到请求的 `similarity_threshold`。缺失、畸形、零值或非有限特征不准许合并，VLM 置信度不是 ArcFace 分数。回归：`tests/test_identity_cluster_arbitration.py`。

## Reference quality

**素材与混合。** 参考质量按有效脸尺寸、裁剪清晰度、检测置信度和姿态评分。API 用质量分加权图库特征，CLI 用均匀权重。best-match 排名由 70% 质量、30% yaw（侧转）兼容度组成；真正模型输入是 **80% 归一化图库特征 + 20% 最佳参考特征**，再归一化，不是一张照片。单参考或 gallery 模式直接用图库；`core/swap_diagnostics.py` 返回向量与来源配方。

**极端侧脸。** 五点绝对 yaw ratio > `0.75` 时，至少一张参考须与目标相差 ≤ `0.45`，否则跳过。二维五点模型难以合成侧脸被隐藏的一半，绕过保护的实测出现双眼/鼻部畸变。跳过原因 `extreme_pose_without_matching_reference`；批任务与同步响应保留其他成功人物，并提示部分结果。素材选择/维护见 [素材库](portrait-library.md)。

**身份强度。** InSwapper 的 `identity_blend_strength` 控制可见源身份贡献：API `0.4–0.95`，默认 `0.9`（its default is `0.9`）；Studio 滑杆 `0.45–0.95`，`0.72` 是显式保守选项。调高强度不补姿态证据，也不绕过侧脸保护、掩码交集或可见变化阈值。[强度回归](../tests/test_strength_contract.py)约束 API/流水线/Studio 默认一致并绑定原英文短句；依据：[默认强度决策](../.agents/notes/swap/20260907-default-strength-072-to-09.md)。

## Per-person execution diagnostics

`person_outcomes[].diagnostics` 只含无数组的诊断证据：

- **`matching`：** 最佳/实际分配余弦、阈值、本图从左到右的 1-based（从 1 起）脸编号，用于识别目标，不是结果与源肖像的相似度。目标特征缺失为 `target_reference_unavailable`；达标但被其他映射占用为 `assignment_conflict`；真实低于阈值为 `identity_match_below_threshold`。
- **`source_selection`：** 模式、参考数、1-based 最佳参考编号、图库/最佳混合系数、L2 归一化前的图库权重。外部图库没有明确来源时，权重为 null、`gallery_weights_known=false`，不猜均匀或质量权重。
- **`mask`：** box/model/dense/dense_fallback/occlusion/glasses/manual 的 applied/disabled/missing/failed 状态及均值、交集均值、最终脸框平均 alpha 和请求强度。异常只记录固定非敏感代码：关键点失败保留 `dense_mask_failed`；默认椭圆成功记 `dense_fallback` applied；回退也失败记 `dense_fallback_failed`，其他掩码仍应用。均值分属不同坐标支持范围，不是脸面积占比或身份分数，见 `tests/test_dense_mask_fallback.py`。

**状态边界。** SWAPPED still means 可见像素变化检查通过，不证明身份或自然度。诊断不含图片、embedding（身份特征向量）、源/模型路径或原始异常；缺诊断的记录明确为未知，历史在 [任务回看](task-history.md) 中只读。

## Single-image mapping identity boundary

**自动与显式绑定。** `detect-faces` 返回本图脸编号和可空 `global_person_id`；null 不代表人物身份。`swapPayload.ts` 自动配对要求全部目标分析完成、每张脸都有有效正整数全局身份，不把未知 ID 分组或省略来猜目标。单图可按显示的本地编号显式绑定。快速/批量请求校验人物与参考编号为正整数；映射对话框排除畸形保存绑定，用户可替换而不丢图。回归：`swapPayload.test.ts`、`MultiPersonMatrixModal.test.tsx`。

源图库提取后只留几何、特征、质量/姿态元数据，加载下一张前释放解码图；可选批量 AI 证据无损落盘，通过受控 URL 使用，见 `tests/test_reference_disk_lifetime.py`。

### 回贴内存范围

[`warp_face_region`](../core/compositing.py) 仅变换对齐裁剪在原画布的投影范围，保留双线性插值外缘、裁到画布内，再用同一 float32 公式融合，不缩小整图。平移目标原点可能引入 OpenCV 少量插值取整差异，不宣称与全画布 warp 完全相同。测试：`tests/test_compositing_region.py`；实测与限制：[内存调度](memory-scheduling.md#精修共享队列缩略图与局部回贴)。

## 模型与许可

- 生产默认及未启用研究模型的 `auto` 使用 `inswapper_128`。启用 `allow_research_models` 后，`auto` 在模型可用、脸尺寸 ≥160、绝对 yaw ≤0.45、身份置信度 ≥0.55 时优先 `hyperswap_1c_256`，否则用 InSwapper；见 [路由实现](../core/model_router.py)、[边界回归](../tests/test_model_quality_routing.py)。InSwapper 权重在 FaceFusion 元数据中为 **Non-Commercial**，分发或商用前须另行取得授权。
- `hyperswap_1c_256`（ResearchRAIL）须启用 `allow_research_models`，显式请求它不受上述自动路由门槛限制；不随便携包分发。`ghost_1_256` 为 Apache-2.0，仅限显式实验。
- 评估用 HyperSwap 1a/1b、AlphaFace、Ghost 2/3 和外部扩散模型仅在 `scripts/` 离线工具中，API/CLI/便携包不加载。
- `scripts/evaluate_identity_similarity.py` 用 ArcFace 余弦复现身份迁移评测，不是自然度验收。
- `no_visible_change` 表示未换脸，常因纯侧脸、手遮脸、帽子、低头导致掩码覆盖过低；是守卫生效，不是缺陷。

依据：[默认模型与许可](../.agents/notes/swap/20261003-default-swap-model-and-licence.md)、[强度与跳过原因](../.agents/notes/swap/20261003-identity-strength-and-skip-causes.md)、[不采用扩散换脸](../.agents/notes/swap/20261003-reject-diffusion-swappers.md)。

## 证据

手动真实图对比目录为 `outputs/diagnostics/facefusion-parity/`。单元覆盖：[`test_facefusion_pipeline.py`](../tests/test_facefusion_pipeline.py) 的柔化/pixel boost 往返/极端姿态/映射，[`test_swapper_policy.py`](../tests/test_swapper_policy.py) 的路由，[`test_task_swap_outcome.py`](../tests/test_task_swap_outcome.py) 的部分结果提示。M01 姿态扩展的摘要及 SCRFD/ArcFace 测量限定范围见 [版本化记录](validation/20261014-portrait-reference-evidence.md)。生产实现：[pipeline.py](../core/pipeline.py)；文档规则：[documentation-governance.md](documentation-governance.md)。
