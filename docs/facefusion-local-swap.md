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
