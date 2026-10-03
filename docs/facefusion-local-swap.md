# FaceFusion local swap parity

Full-canvas alpha compositing computes float32 intermediates in 256-row blocks with pixel-identical results.
Upload decoding, local inference and batch processing share bounded queues and OS memory headroom checks;
see [memory scheduling and measured allocations](memory-scheduling.md). This changes memory lifetime, not
resolution, identity matching, mask semantics or the blend formula.

PersonaWeave's production local swap path follows the processing sequence used by FaceFusion at commit `4b1dedb853e4838ca7f3cf70b572be241aee2497` (https://github.com/facefusion/facefusion):

1. canonical five-point alignment using the model template. The forward warp's interpolation is chosen from the transform's actual scale factor (`INTER_AREA` when shrinking, `INTER_CUBIC` when the pixel-boost destination enlarges the source content — `INTER_AREA` degrades to near-`INTER_NEAREST` on enlargement per OpenCV's own documented behavior, so it is not used unconditionally);
2. FaceFusion-style pixel boost for target faces larger than the native model input;
3. model inference on aligned tiles and exact interleaved reassembly;
4. mask intersection: soft box mask, 106-point anatomical mask when available, XSeg occlusion mask, and a BiSeNet semantic region-protection mask. If landmark extraction raises, the pipeline still builds the default anatomical ellipse in 512×512 space with 31×31 Gaussian feathering, resizes it to the actual crop and intersects it with the other masks. Extraction failure remains visible in diagnostics rather than being reported as measured landmarks. XSeg and BiSeNet feathering scale with the actual crop width (256px baseline sigma 5; BiSeNet uses sigma 2.5 at 128px). The BiSeNet mask (`core/region_parser.py`, `models/bisenet_resnet_34.onnx`, 19-class CelebAMask-HQ parsing) protects the `glasses` class by default, including classified lens areas; this protection is not removed to inflate identity scores;
5. boundary-ring color correction: a full-crop Reinhard LAB harmonization is computed against the original (unswapped) aligned crop, but only its narrow mask-edge transition ring is blended into the swapped result — the swapped face's own interior tone is never touched. This targets exposure/white-balance seams between the source identity photo and the target scene without the whole-face color/identity drift a full-face transfer risks. Disabled when `color_transfer_mode="off"`;
6. inverse affine alpha paste-back, with manual protection subtracted last.

All model adapters in `core/swapper.py` expose `infer_aligned()` for inference on an already aligned crop. Alignment and compositing belong exclusively to `PersonaWeavePipeline.swap_single_face_facefusion()`; adapters do not provide a second paste-back path. The pipeline consumes the normalized alignment templates declared in `core/swapper.py`, allocating a scaled copy for each crop. Production mask and compositing behavior is covered by `tests/test_facefusion_pipeline.py` and `tests/test_swap_diagnostics.py`.

The production path does not use Poisson cloning, whole-face color transfer, or heuristic hair protection. The former `swap_single_face_seamless()` diagnostic variant is retired and these operations are not supported alternatives: they can alter facial structure, identity, or protected pixels. Ghost remains explicit-only; automatic routing chooses InSwapper unless the user opts into a suitable HyperSwap ResearchRAIL path.

Post-swap restoration is a narrower exception: `core/restorer.py` (`CodeFormerRestorer`) is an opt-in (`enable_face_restoration`, default off) production capability, not a diagnostic. It exists because both swap models cap their working crop at 256x256, which is visibly softer than the surrounding pixels once warped into a high-megapixel photo. Unlike the retired full-face 0.1.0 restoration, its output is blended back exclusively through `crop_mask` -- the same dense-landmark/occlusion/region-protection mask gating the swap itself -- so it cannot reach background, occluded, or region-protected pixels, and `restoration_fidelity_weight` uses CodeFormer's own identity control to bias toward the already-swapped face. GFPGAN has no equivalent control and stays retired.

## Execution providers

All production model constructors use `utils/gpu_helper.py::local_session_options` to disable idle ORT intra/inter-op spinning while retaining automatic thread count and unchanged inference math. The six serially used pools otherwise compete with each other. Health reports actual loaded session providers, not merely package capabilities; kernel placement still requires profiling. CPU timing/pixel evidence and queue diagnostics: [memory scheduling](memory-scheduling.md#排队可观测性与线程竞争).

Windows CPU/GPU portable editions use this same pipeline. `utils/gpu_helper.py` checks the system NVIDIA driver and device count, preloads bundled CUDA/cuDNN DLLs and checks the CUDA provider's static dependency chain before selecting CUDA. Missing drivers/libraries select CPU. Linux retains its existing shared-library preload and driver guard. Actual Windows GPU execution is verified by profiling all six shipped ONNX models, not by `get_available_providers()` alone; see [portable delivery](windows-portable.md) and [native validation](windows-portable-validation.md).

## BiSeNet model provenance

`models/bisenet_resnet_34.onnx` previously held a 15-byte `Entry not found` text stub (a failed-download artifact, not a valid ONNX asset) and was absent from `scripts/download_models.py`'s `MODEL_URLS`. The filename matches FaceFusion's own model exactly: vendor yakhyo, MIT license, hosted at `facefusion/models-3.0.0` on Hugging Face. It has been re-downloaded (SHA-256 `4a0b8c958a3c938913bd06a8365dbb3c8761afba6ecbf0d14b3b1f77eb230c96`, matching the CDN's `x-linked-etag`) and added to the default download set. Its I/O contract was confirmed both from FaceFusion's `face_masker.py`/`choices.py` source and by running real inference against `tests/fixtures/source-01.png`: input tensor `input` at 512×512 (BGR→RGB, ImageNet-normalized, NCHW), output `[0][0]` a `(19, 512, 512)` logit map matching the standard CelebAMask-HQ class order (0 background, 1 skin, 2/3 eyebrows, 4/5 eyes, 6 glasses, 7/8 ears, 9 earring, 10 nose, 11 mouth, 12/13 lips, 14 neck, 15 necklace, 16 cloth, 17 hair, 18 hat). `models/face_parser.onnx` was the other placeholder found alongside it; it has no counterpart anywhere in FaceFusion's model registry and was deleted rather than replaced.

## Multi-person mapping assignment

When a batch request supplies `person_mappings` for a group photo, `resolve_person_mapping_assignment()` (`core/pipeline.py`) solves a globally optimal one-to-one assignment between mappings and detected faces via the Hungarian algorithm (`scipy.optimize.linear_sum_assignment`) over the full cosine-similarity matrix, rather than letting each mapping greedily claim its own best-similarity face in list order. Below-threshold pairs are masked out before optimizing and re-verified against the real similarity afterward, so a pair the algorithm was forced into by a saturated row/column is still rejected correctly.

Cross-image clustering is a separate operation in `api/routes/faces.py`: VLM may suggest merging an orphan into a cluster, but `VLMIdentityArbiter.refine_clusters()` rechecks the cosine similarity between the actual ArcFace reference used for arbitration and the orphan against the request's `similarity_threshold`. Missing, malformed, zero or non-finite embeddings cannot authorize a merge; VLM confidence is not an ArcFace score. This does not replace the Hungarian assignment above. Offline coverage: `tests/test_identity_cluster_arbitration.py`.

## Reference quality

Uploaded references are scored from effective face size, crop sharpness, detector confidence, and pose. The API weights gallery embeddings by this score; the CLI currently uses uniform gallery weights. Best-match ranks references with 70% quality and 30% yaw compatibility, but its model input is **80% normalized gallery embedding plus 20% best-reference embedding**, normalized again. It is not a single selected source photograph. With one reference, or gallery mode, the existing gallery is used directly. `core/swap_diagnostics.py` returns both the unchanged vector and its source recipe.

The portrait-library UI deterministically preselects at most eight non-contact-sheet assets, prioritizing a frontal view, the strongest available left/right profiles, left/right chin-down profiles, and near-30° left/right views before filling any remaining slots. It does not randomly choose six images. The executable policy is `selectDiversePortraitAssets()` in `web/src/utils/portraitAssetSelection.ts`.

For a target with absolute five-point yaw ratio above `0.75`, local swapping is skipped unless at least one source reference is within `0.45` yaw-ratio units. A 2D five-point swapper cannot reliably synthesize the hidden half of an extreme profile; bypassing this guard produced doubled eyes and distorted noses. The skip is reported as `extreme_pose_without_matching_reference`, and batch/sync responses preserve successful people while surfacing an actionable partial-result warning.

Studio shows single-image refusals, failed batch images and partial-person warnings in an acknowledgement dialog. HTTP errors preserve the server's `detail`; a completed batch aggregates failures by image and notifies only once per task, rather than opening a dialog on every poll. Reference-library maintenance and archived-identity semantics are documented in [portrait-library.md](portrait-library.md). Notification regressions are in `web/src/components/ui/ToastProvider.test.tsx` and `web/src/components/studio/BatchControls.test.tsx`.

`identity_blend_strength` controls the visible source-identity contribution on the InSwapper production path. Its API range is `0.4–0.95` and its default is `0.9`; the Studio slider exposes `0.45–0.95`. The previous `0.72` remains an explicit conservative choice. Raising strength cannot create missing pose evidence or bypass the extreme-profile guard, mask intersection, or the unchanged visible-change threshold. The default is synchronized across API, pipeline and Studio by a regression test; see [the measured four-image comparison](./swap-benchmarks.md#recorded-strength-comparison).

## Per-person execution diagnostics

New `person_outcomes[].diagnostics` records safe, array-free evidence:

- `matching`: best and assigned cosine similarity, threshold, and 1-based left-to-right face index within the current image. This is target identification evidence, not the similarity of the generated output to a source portrait. Missing target features use `target_reference_unavailable`; a qualifying match lost to another mapping uses `assignment_conflict`; genuinely sub-threshold candidates retain `identity_match_below_threshold`. Hungarian one-to-one allocation and both threshold checks remain unchanged.
- `source_selection`: mode, reference count, 1-based best-reference index, gallery/best blend coefficients, and gallery reference weights before gallery L2 normalization. API and CLI pass their actual weights. An external gallery without explicit provenance has null weights and `gallery_weights_known=false`, never guessed uniform or quality weights.
- `mask`: named components (box/model/dense/dense_fallback/occlusion/glasses/manual), applied/disabled/missing/failed states, component means, intersection mean, final face-box mean alpha and requested strength. Exceptions become fixed non-sensitive failure codes. A landmark exception retains `dense_mask_failed`; successful default ellipse protection is separately marked `dense_fallback` applied. If fallback construction itself fails, `dense_fallback_failed` remains visible and the other masks still apply. Existing graceful degradation remains, but missing/failed protection is now visible instead of only appearing in terminal logs. Means use different coordinate supports and are not interchangeable with a face-area percentage or an identity score. Pixel regressions for the fallback are in `tests/test_dense_mask_fallback.py`.

Diagnostics contain no image data, embedding, source/model path or raw exception. They are advisory execution evidence; SWAPPED still means the unchanged visible pixel-change check passed, not that identity or naturalness was verified. Old records without diagnostics stay explicitly unknown. Historical results and failures are read-only in the [task history dialog](./task-history.md).

## Regression fixture

The manual real-image comparison is written under `outputs/diagnostics/facefusion-parity/`. Unit coverage for mask softness, pixel-boost round trips, extreme-pose rejection, and mapping outcomes lives in [`tests/test_facefusion_pipeline.py`](../tests/test_facefusion_pipeline.py). Model routing is covered by [`tests/test_swapper_policy.py`](../tests/test_swapper_policy.py), partial-result messages by [`tests/test_task_swap_outcome.py`](../tests/test_task_swap_outcome.py), and the generated M01 pose expansion records checksums plus SCRFD/ArcFace measurements in [`data/portrait_expansions/README.md`](../data/portrait_expansions/README.md).

The production implementation referenced by this document is [`core/pipeline.py`](../core/pipeline.py). Documentation authority and synchronization rules are defined in [documentation-governance.md](./documentation-governance.md).

## Single-image mapping identity boundary

`detect-faces` returns image-local face indices and a nullable `global_person_id`; null does not identify a person. Automatic pairing in `swapPayload.ts` requires all targets analyzed and every face to have a valid positive integer global identity. Unknown IDs cannot be grouped or omitted to infer a unique target. A single photo can instead be explicitly bound using its displayed local face indices. Both quick and batch requests validate positive integer person/reference indices; the mapping dialog excludes malformed saved bindings so the user can replace them without losing images. Regression: `swapPayload.test.ts`, `MultiPersonMatrixModal.test.tsx`.

Source galleries retain face geometry, embeddings and quality/pose metadata after each image is analyzed; decoded source images are released before the next source loads. Optional batch AI evidence is frozen losslessly to disk and passed through controlled artifact URLs. Local identity selection and swap pixels are unchanged; see [memory scheduling](memory-scheduling.md) and `tests/test_reference_disk_lifetime.py`.

### 回贴内存范围

生产回贴使用 `core/compositing.py::warp_face_region` 限定到对齐裁剪在原画布上的投影范围，
保留双线性插值外缘并裁到画布内，再用同一 float32 融合公式更新该区域；不会缩小整幅图。
原点平移可能引入 OpenCV 的少量插值取整差异，不能宣称与旧全画布 warp 完全相同。
画布边缘/旋转/缩放/无交集及诊断均值测试见 `tests/test_compositing_region.py`；
内存实测与适用限制见 [内存调度](memory-scheduling.md#精修共享队列缩略图与局部回贴)。

## 身份相似度实测（2026-10-03，真实照片 × 素材库）

[`scripts/evaluate_identity_similarity.py`](../scripts/evaluate_identity_similarity.py)（非生产）把 13 张真实合影中 35 张
≥200px 的人脸，经生产多人映射路径（素材库参考 → 生产画廊/best-match → `process_image_multi_mapping`，默认参数）
各换成 3 个同性别素材库人物，共 105 次尝试；再用同一 ArcFace 把结果脸与全部 64 位素材库人物比较。种子 20261003，
报告 SHA-256 `501715da36b716c76a1a0a1ced33083eb31f19d483026c8fab8201a50f373a0c`（含真人图片的输出只留在本机
`outputs/diagnostics/`，不入库）。分母始终是全部尝试，未换脸计为未命中。

| 指标 | 结果 |
| --- | --- |
| 成功换脸并评分 / 尝试 | 87 / 105；18 次 `no_visible_change`，来自 6 张脸（5 张侧脸 yaw≈1、1 张正脸），均为男性 |
| 结果脸与源人物相似度（画廊质心 / 逐参考均值） | 均值 0.523 / 0.423；换脸前原脸与源人物仅 0.038 |
| 在 64 人中排第 1（占全部尝试 / 占已评分） | 58% / 70%（同性别随机基线 3%）；Top5 79% |
| 与最佳其他人物的平均间隔 | 0.038（很小） |
| 结果脸与原脸相似度 | 均值 0.398；29% 的结果脸更像原脸而不是目标源人物 |
| 达到素材库内 1% 误识阈值（同性别，质心 0.737 / 逐参考 0.598） | 约 7% |

校准用素材库自身：同一人留一参考的真对为 0.776（逐参考 0.627），同性别不同人 0.560（逐参考 0.452）。
素材库人物彼此很像（合成人像），故绝对阈值偏高；两种评分方式结论一致。分层显示女性（排第 1 为 78%）高于男性（48%，
含更多侧脸跳过）。

结论仅限：生产配置下身份只**部分**迁移，明显高于随机但低于同一人的相似度，原脸特征仍有保留。
不代表自然度；结果取自 WSL 中 onnxruntime-gpu 1.20.2，与 Windows 便携包（1.23.2）数值可能略有差异；
样本是一台机器、一次、3 个随机人物，且素材库真对/冒认分布不等同于真人之间的分布。
回归：`tests/test_identity_similarity_report.py`（仅覆盖校准、排名和聚合的纯计算）。

### 跳过原因与 `identity_blend_strength` 扫描（2026-10-03）

**跳过的脸。** 上述 18 次 `no_visible_change` 来自 6 张脸（诊断：生产 `mask` 分量）。它们的最终掩码覆盖率
`intersection_mean` 仅 0.06–0.14（成功换脸的中位数 0.25），遮挡（XSeg）分量均值中位数 0.11 对 0.27；
人工查看这 6 张是纯侧脸、手遮脸、戴帽和低头。这是守卫按设计工作：没有可换的可见面部区域就如实跳过，
不是误判；提高强度不会绕过它（本节扫描中 0.95 仍有 16/105 跳过）。已知不足：状态仅写 `no_visible_change`，
没有给用户“遮挡/侧脸”提示，这属于可改进的可观测性，尚未实现。

**强度扫描。** 同一 35 张脸、同一随机身份分配（种子 20261003），仅改 `identity_blend_strength`；
报告（schema 2）SHA-256 `f91eb95248743de6f5efb57f3da1c8f76ed4d4b968fb1227595b1f2fb6c978af`。分母为全部 105 次尝试。

| 强度 | 已评分 | 质心相似度 | 排第 1 | Top5 | 与原脸相似度 | 达 1% 误识阈值 | 脸框平均像素差 | 结果检测分 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.72 | 68 | 0.467 | 41.9% | 59.0% | 0.524 | 0% | 3.94 | 0.830 |
| 0.80 | 81 | 0.493 | 51.4% | 70.5% | 0.467 | 1.9% | 4.17 | 0.822 |
| 0.90（默认） | 87 | 0.523 | 58.1% | 79.0% | 0.398 | 6.7% | 4.58 | 0.821 |
| 0.95（API 上限） | 89 | 0.528 | 59.0% | 82.9% | 0.364 | 7.6% | 4.79 | 0.821 |

在三档都评分的 68 张脸上做配对：0.72→0.90 相似度平均 +0.074（68/68 提升），0.90→0.95 仅 +0.013（66/68 提升）。
单调但边际递减：默认 0.9 已拿到大部分收益，0.95 额外收益很小。像素差和检测分只是自然度代理，变化很小且未显示
退化信号；拼图（原脸｜0.72｜0.8｜0.9｜0.95｜源参考）中各强度肉眼差异也很小。**这不是自然度验收**。
关键结论：强度不是主要瓶颈——即使 0.95，换入后与原脸仍有 0.36 相似度，仅约 8% 达到误识阈值，
说明限制来自 InSwapper 128 的身份注入能力、画廊/best-match 的 0.8/0.2 混合以及掩码覆盖率，而不是滑块。
其他换脸模型见下一节；面部修复、像素放大仍未测试，不能据此推断它们的效果。**不修改默认值。**
样本限制同上：一台机器、一次、3 个随机人物。

### 换脸模型对比（2026-10-03）

[`scripts/evaluate_identity_similarity.py --arms`](../scripts/evaluate_identity_similarity.py) 在同一 35 张真实人脸、同一随机
身份分配（种子 20261003）上，用显式模型请求对比三个后端，其他参数为默认值；权重哈希与 `models/README.md` 一致。
路由若悄悄回退到别的模型，该次记为 `model_fallback` 而不计入该模型（本次未发生）。报告（schema 2）SHA-256
`3592c4ff39591645d8fbc06bdd211d54a8b08f8ef30e70cb764b46ed11d6bd73`。分母为全部 105 次尝试。HyperSwap 为 **ResearchRAIL**，仅限本地研究评估，未随便携包分发，商用前须许可审查；
Ghost 为 Apache-2.0。

| 模型 | 已评分 | 质心相似度 | 排第 1 | Top5 | 与原脸相似度 | 更像原脸的占比 | 达 1% 误识阈值 | 脸框平均像素差 | 总换脸耗时 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| InSwapper 128（默认 0.9） | 87 | 0.523 | 58.1% | 79.0% | 0.398 | 28.6% | 6.7% | 4.58 | 54 s |
| HyperSwap 1c 256 | 92 | 0.562 | 75.2% | 85.7% | 0.270 | 9.5% | 13.3% | 5.44 | 97 s |
| Ghost 1 256 | 100 | 0.433 | 63.8% | 81.9% | 0.234 | 27.6% | 0% | 7.73 | 68 s |

未评分：InSwapper 18 次跳过；HyperSwap 11 次跳过 + 2 次结果脸未检出；Ghost 3 次跳过 + 2 次结果脸未检出。
在三个模型都评分的 85 张脸上配对：HyperSwap 相对 InSwapper 相似度平均 +0.052（76/85 更高），排第 1 的比例
89.4% 对 71.8%；Ghost 相对 InSwapper 平均 −0.058（仅 18/85 更高）。HyperSwap 对侧脸也更少跳过，
单次整图耗时约为 InSwapper 的 1.8 倍（含检测与掩码，非纯模型耗时）。

人工查看拼图（原脸｜InSwapper｜HyperSwap｜Ghost｜参考，16 张均匀抽样）：HyperSwap 保持了结构且改变更明显；
Ghost 绝对相似度最低、像素改动最大，且多处出现嘴唇过红、五官结构不稳定，与路由器注释中“Ghost 在部分跨身份迁移上
面部结构不稳定”一致。像素差与检测分只是代理指标，**这不是自然度验收**，也没有使用视觉大模型评审。
结论限制：一台机器、一次、3 个随机人物，WSL 的 onnxruntime-gpu 1.20.2；HyperSwap 在本评测中身份迁移更强，
但因许可限制不能直接并入便携包；`auto` 路由默认仍只用 InSwapper，本评测不修改任何默认值或路由。

### 更多 FaceFusion 模型与许可对照（2026-10-03）

**许可（取自 FaceFusion master 的 `face_swapper/core.py` 的 `__metadata__.license`，2026-10-03 核对）。**
inswapper_128 / inswapper_128_fp16、alphaface_256、simswap_256、simswap_unofficial_512、blendswap_256 为 Non-Commercial；
hyperswap_1a/1b/1c_256 为 ResearchRAIL；ghost_1/2/3_256（ai-forever）为 Apache-2.0；uniface_256、hififace_unofficial_256 为 Unknown。
FaceFusion 3.9.1（2026-09-30）默认模型是 `hyperswap_1a_256`。**本项目默认的 InSwapper 本身是非商用许可**，
商用须另行取得授权；FaceFusion 目录中许可明确宽松的只有 Ghost 系列。

**扩展对比。** 评估适配器 [`scripts/swap_eval_adapters.py`](../scripts/swap_eval_adapters.py)（非生产，只注入评测管线实例，
API/CLI/便携包不会加载）接入 HyperSwap 1a/1b、AlphaFace 和 Ghost 2/3；权重放在 `models/eval/`（已 gitignore）。
来源与摘要见 `models/README.md`；张量接口用 onnxruntime 核对（HyperSwap 1a/1b 与 1c 相同，Ghost 2/3 与 Ghost 1 相同，
AlphaFace 输入 `target`/`source`、输出无掩码）。同一 35 张真实人脸与身份分配（种子 20261003）、默认参数，8 个模型各 105 次尝试，
报告 SHA-256 `275c1021153c99c9161b05a11b65f26243f98bb4fac9a0f2f9c7dab99e8884bd`；InSwapper 臂与之前的结果逐位一致。

| 模型（许可） | 已评分 | 质心相似度 | 排第 1 | Top5 | 与原脸相似度 | 达 1% 误识阈值 | 脸框平均像素差 | 跳过 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| InSwapper 128（非商用，默认） | 87 | 0.523 | 58.1% | 79.0% | 0.398 | 6.7% | 4.58 | 18 |
| HyperSwap 1a（ResearchRAIL，FaceFusion 默认） | 85 | 0.556 | 65.7% | 80.0% | 0.301 | 7.6% | 4.36 | 20 |
| HyperSwap 1b（ResearchRAIL） | 89 | 0.573 | 70.5% | 82.9% | 0.289 | 9.5% | 4.58 | 14 |
| HyperSwap 1c（ResearchRAIL） | 92 | 0.562 | 75.2% | 85.7% | 0.270 | 13.3% | 5.44 | 11 |
| AlphaFace 256（非商用） | 98 | 0.469 | 63.8% | 87.6% | 0.311 | 1.0% | 6.34 | 7 |
| Ghost 1（Apache-2.0） | 100 | 0.433 | 63.8% | 81.9% | 0.234 | 0% | 7.73 | 3 |
| Ghost 2（Apache-2.0） | 102 | 0.423 | 67.6% | 86.7% | 0.240 | 1.0% | 7.43 | 3 |
| Ghost 3（Apache-2.0） | 101 | 0.415 | 51.4% | 85.7% | 0.245 | 0% | 7.41 | 3 |

在 8 个模型都评分的 78 张脸上配对（相对 InSwapper 的平均相似度变化）：HyperSwap 1a +0.034（57/78 更高）、1b +0.053（68/78）、
1c +0.051（69/78）；AlphaFace −0.015（26/78）；Ghost 1/2/3 分别 −0.062、−0.069、−0.082。

- **HyperSwap 三个版本都高于 InSwapper**，1b/1c 最强；FaceFusion 当前默认的 1a 在其中最弱，但仍高于 InSwapper。
- **AlphaFace** 平均相似度略低于 InSwapper，但跳过最少（7 对 18）、Top5 最高（87.6%），对侧脸覆盖更好；
  源向量缩放系数取 1/8/15.5/30 结果完全相同，说明模型内部会归一化，标定系数不影响结论。
- **Ghost（唯一宽松许可）三个版本都不如 InSwapper**：绝对相似度低 0.06–0.08，几乎无结果达到误识阈值；
  Ghost 2 相对最好（排第 1 为 67.6%），Ghost 3 最差。拼图中 Ghost 系列嘴唇/口型改变和结构不稳定更常见。
  因此**没有“许可干净且更好”的现成选择**。
- Ghost 3 的总耗时为 967 s（其他模型 36–60 s），原因未查明且可能与本机内存压力有关，不作为速度结论。
- 局限同前：一台机器、一次、3 个随机人物、WSL 的 onnxruntime-gpu 1.20.2；像素差与检测分只是自然度代理，
  拼图为人工查看，不是自然度验收；研究/非商用权重仅用于本地评估，未分发、不进入便携包，默认模型与路由不变。

### 扩散换脸方案评估：REFace 与 DiffSwap++（2026-10-03）

评估两个可本地运行的开源扩散换脸模型（均基于 Stable Diffusion 1.4、50 步 DDIM，GitHub：
`Sanoojan/REFace` 提交 `f73bd8d`，`WestonBond/DiffSwapPP` 提交 `4b8f788`，代码 MIT；REFace 权重 CC-BY-NC 4.0）。
**仅本机研究评估，未进入便携包或生产路径。**

**安全与复现。** 检查点是 PyTorch pickle，加载前用 `pickletools` 静态扫描（不执行），只引用张量重建类、
`OrderedDict`，DiffSwap++ 另有 numpy 标量和 Lightning `ModelCheckpoint`；运行时用白名单 `torch.load` 替换，其他类一律拒绝，
并用内存映射加载。权重 SHA-256：REFace `last.ckpt` `194081de532a6bab92c5e135e6d950f859aafd4720e0a491152a3469f0762d10`，
DiffSwap++ `last.ckpt`（12.9 GB）`90088755d2c727d7688042740be02c80f3262a8dcaf299c5d2471e2c5efd6a86`。
隔离环境：Python 3.10、torch 2.2.2+cu121，OpenAI CLIP 固定到提交 `dcba3cb2`；上游仅为 `import` 而需要的训练专用包
`taming`、`wandb` 用本地占位模块代替（使用即报错）。成对运行器由
[`scripts/diffusion_pair_patch.py`](../scripts/diffusion_pair_patch.py) 从上游脚本精确替换生成，
目标/源准备见 [`scripts/diffusion_swap_prepare.py`](../scripts/diffusion_swap_prepare.py)，
打分见 [`scripts/evaluate_diffusion_swaps.py`](../scripts/evaluate_diffusion_swaps.py)。

**覆盖率。** 两个模型都依赖 dlib 正脸检测做对齐。在 35 个目标脸中，dlib 只检出 18 个（51%），17 个（多为侧脸、低头）
无法进入流程；源人像（正面）全部可检出。以下比较只针对这 18 个可对齐的脸，且都在扩散模型自己的 FFHQ 式对齐裁剪（外扩
边缘复制以便检测）上进行，用同一身份分配和同一套 ArcFace 指标，报告 SHA-256：基线
`d61c78241495ec621d3080839bc82d0749e5fdba897d3570224425690b894c13`、单参考对照
`3ee7e0d58d9499c3a619841062780781ac5bade2e03c9c322054bfba833e8e13`、REFace
`df999ec7fda97da97ecea9948ba90bfac51cc96b2660c316ab92a816f82c8a6d`、DiffSwap++
`cae3b8dca5e24247203f87774ab150a8596a8a591453ae8433487baa6c38f01a`。

扩散工具只接受**一张**源图，因此以单张正面参考为对照（生产管线只给正面参考时，对极端侧脸会按
`extreme_pose_without_matching_reference` 守卫拒绝，18 个里 6 个）。在所有臂都能评分的 12 个脸上：

| 模型（源图） | 质心相似度 | 逐参考均值 | 排第 1 | 与原脸相似度 |
| --- | ---: | ---: | ---: | ---: |
| InSwapper 128（单张正面参考） | 0.530 | 0.424 | 75% | 0.344 |
| HyperSwap 1b / 1c（单张正面参考） | 0.531 / 0.540 | 0.425 / 0.432 | 67% / 75% | 0.288 / 0.271 |
| AlphaFace（单张正面参考） | 0.504 | 0.404 | 83% | 0.280 |
| **REFace**（单张正面参考） | **0.313** | 0.252 | 42% | 0.237 |
| **DiffSwap++**（单张正面参考，仅 4 个可比脸） | **0.337** | 0.272 | 50% | 0.237 |

18 个可对齐脸上，画廊（16 张）臂：InSwapper 0.585、HyperSwap 1c 0.625、AlphaFace 0.570、Ghost 2/3 0.493/0.489；
REFace 0.302、DiffSwap++（前 6 个脸）0.325，**没有一个扩散结果达到 1% 误识阈值**。
扩散模型在 6 个被单参考守卫拒绝的极端侧脸上都能出图，但身份迁移同样低（REFace 约 0.28）。

人工查看拼图（目标｜REFace｜DiffSwap++｜源）：两者结果几乎相同，保持姿态、发型和眼镜，面部被明显改动，
但嘴唇偏粉紫、肤色斑块等伪影较常见，不如 ONNX 换脸模型稳定。

**成本。** 单对约 5.5 分钟（GPU 无其他占用），内存/显存紧张时达 7–16 分钟，显存占用 7.7–7.9 GB（总 8 GB）；
ONNX 换脸为秒级。DiffSwap++ 因此只跑了 18 对中的前 6 对。

**重要限制。** DiffSwap++ 论文模型依赖 3D 潜变量条件，但其发布的 `inference_swap_selected.py` 与 REFace 的相同、
不传入 3D 特征文件名，3D 分支（仅在传入特征名时执行）没有被使用；所以这里测的是“按发布推理脚本运行的 DiffSwap++ 检查点”，
不等于论文完整配置，**结论不能外推到论文报告的指标**。其余限制同前：一台机器、一次、3 个随机人物中的 18 个脸，
样本小；不是自然度验收。结论：在本项目的设置下，这两个扩散方案的身份迁移明显弱于 InSwapper/HyperSwap，
且慢三个数量级、覆盖率低，**不值得替换当前 ONNX 流程**。
