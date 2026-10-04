# FaceFusion local swap parity

Production local swap follows the processing sequence of FaceFusion commit `4b1dedb853e4838ca7f3cf70b572be241aee2497` (https://github.com/facefusion/facefusion). Full-canvas compositing runs in 256-row float32 blocks with pixel-identical results; upload decoding, local inference and batch processing share bounded queues and OS memory headroom checks ([memory-scheduling.md](memory-scheduling.md)). Memory lifetime changes nothing about resolution, identity matching, mask semantics or the blend formula.

1. Canonical five-point alignment with the model template. The forward warp picks its interpolation from the transform's actual scale: `INTER_AREA` when shrinking, `INTER_CUBIC` when pixel boost enlarges the content (`INTER_AREA` degrades to near-`INTER_NEAREST` on enlargement).
2. FaceFusion-style pixel boost for faces larger than the native model input.
3. Model inference on aligned tiles and exact interleaved reassembly.
4. Mask intersection: soft box mask, 106-point anatomical mask, XSeg occlusion mask and a BiSeNet semantic region-protection mask. If landmark extraction raises, the pipeline builds the default anatomical ellipse in 512×512 space with 31×31 Gaussian feathering, resizes it to the crop and intersects it with the other masks; the failure stays visible in diagnostics instead of being reported as measured landmarks. XSeg and BiSeNet feathering scale with the actual crop width (256px baseline sigma 5; BiSeNet sigma 2.5 at 128px). The BiSeNet mask (`core/region_parser.py`, `models/bisenet_resnet_34.onnx`, 19-class CelebAMask-HQ) protects `glasses` by default, including lens areas; this protection is never removed to inflate identity scores.
5. Boundary-ring color correction: a full-crop Reinhard LAB harmonization against the unswapped aligned crop is computed, but only its narrow mask-edge ring is blended in; the swapped face's interior tone is never touched. This targets exposure/white-balance seams without whole-face drift. Off when `color_transfer_mode="off"`.
6. Inverse affine alpha paste-back, with manual protection subtracted last.

All adapters in `core/swapper.py` expose `infer_aligned()` for an already aligned crop; alignment and compositing belong only to `PersonaWeavePipeline.swap_single_face_facefusion()`, and adapters offer no second paste-back path. Templates are normalized in `core/swapper.py`, scaled per crop. Coverage: `tests/test_facefusion_pipeline.py`, `tests/test_swap_diagnostics.py`.

Poisson cloning, whole-face color transfer and heuristic hair protection are not supported: they can alter facial structure, identity or protected pixels (`swap_single_face_seamless()` is retired, not an alternative). Ghost is explicit-only; automatic routing picks InSwapper unless the user opts into a suitable HyperSwap path.

Post-swap restoration is opt-in (`enable_face_restoration`, default off): `core/restorer.py` (`CodeFormerRestorer`) recovers detail lost to the 256×256 working-crop cap. Its output is blended back only through `crop_mask`, the same dense-landmark/occlusion/region-protection mask that gates the swap, so it cannot reach background, occluded or protected pixels, and `restoration_fidelity_weight` uses CodeFormer's identity control to bias toward the swapped face. GFPGAN has no such control and is retired.

## Execution providers

All production model constructors use `utils/gpu_helper.py::local_session_options` to disable idle ORT intra/inter-op spinning (thread count and inference math unchanged), because six serially used pools otherwise compete. Health reports the actual loaded session providers, not package capability; kernel placement still needs profiling ([memory-scheduling.md](memory-scheduling.md#排队可观测性与线程竞争)). Windows CPU/GPU editions use the same pipeline: `utils/gpu_helper.py` checks the system NVIDIA driver and device count, preloads bundled CUDA/cuDNN DLLs and checks the CUDA provider's dependency chain before selecting CUDA; missing drivers or libraries select CPU. Linux keeps its shared-library preload and driver guard. Windows GPU execution is verified by profiling all six shipped ONNX models, not by `get_available_providers()` ([windows-portable.md](windows-portable.md), [windows-portable-validation.md](windows-portable-validation.md)).

## BiSeNet model provenance

`models/bisenet_resnet_34.onnx` is FaceFusion's model (vendor yakhyo, MIT, `facefusion/models-3.0.0` on Hugging Face) in the default download set, SHA-256 `4a0b8c958a3c938913bd06a8365dbb3c8761afba6ecbf0d14b3b1f77eb230c96`; a 15-byte `Entry not found` file with this name is a failed-download stub. I/O confirmed from FaceFusion's source and real inference on `tests/fixtures/source-01.png`: input `input` 512×512 (BGR→RGB, ImageNet-normalized, NCHW), output `[0][0]` a `(19, 512, 512)` logit map in the CelebAMask-HQ order (0 background, 1 skin, 2/3 eyebrows, 4/5 eyes, 6 glasses, 7/8 ears, 9 earring, 10 nose, 11 mouth, 12/13 lips, 14 neck, 15 necklace, 16 cloth, 17 hair, 18 hat).

## Multi-person mapping assignment

For `person_mappings` on a group photo, `resolve_person_mapping_assignment()` (`core/pipeline.py`) solves a globally optimal one-to-one assignment between mappings and faces with the Hungarian algorithm (`scipy.optimize.linear_sum_assignment`) over the full cosine-similarity matrix, instead of each mapping greedily claiming its best face in list order. Below-threshold pairs are masked before optimizing and re-verified against the real similarity afterwards, so a pair forced by a saturated row or column is still rejected.

Cross-image clustering is separate (`api/routes/faces.py`): a VLM may suggest merging an orphan into a cluster, but `VLMIdentityArbiter.refine_clusters()` rechecks the ArcFace cosine between the reference used for arbitration and the orphan against the request's `similarity_threshold`. Missing, malformed, zero or non-finite embeddings cannot authorize a merge, and VLM confidence is not an ArcFace score. Coverage: `tests/test_identity_cluster_arbitration.py`.

## Reference quality

References are scored from effective face size, crop sharpness, detector confidence and pose. The API weights gallery embeddings by this score; the CLI uses uniform weights. Best-match ranks references with 70% quality and 30% yaw compatibility, but its model input is **80% normalized gallery embedding plus 20% best-reference embedding**, normalized again, not a single photograph. With one reference, or in gallery mode, the gallery is used directly. `core/swap_diagnostics.py` returns the vector and its source recipe.

For a target with absolute five-point yaw ratio above `0.75`, local swapping is skipped unless some reference is within `0.45` yaw-ratio units, because a 2D five-point swapper cannot synthesize the hidden half of an extreme profile (bypassing the guard produced doubled eyes and distorted noses). The skip is `extreme_pose_without_matching_reference`; batch and sync responses keep successful people and surface a partial-result warning. Reference selection and library maintenance: [portrait-library.md](portrait-library.md).

`identity_blend_strength` controls the visible source-identity contribution on the InSwapper path: API range `0.4–0.95`, its default is `0.9`, Studio slider `0.45–0.95`, and `0.72` stays an explicit conservative choice. Raising it cannot create missing pose evidence or bypass the extreme-profile guard, mask intersection or visible-change threshold. A regression test keeps the default identical across API, pipeline and Studio; basis: [decision note](../.agents/notes/swap/20260907-default-strength-072-to-09.md).

## Per-person execution diagnostics

`person_outcomes[].diagnostics` holds safe, array-free evidence:

- `matching`: best and assigned cosine similarity, threshold and the 1-based left-to-right face index in this image. This identifies the target, not the similarity of the output to a source portrait. Missing target features give `target_reference_unavailable`, a qualifying match lost to another mapping gives `assignment_conflict`, and genuinely sub-threshold candidates keep `identity_match_below_threshold`.
- `source_selection`: mode, reference count, 1-based best-reference index, gallery/best blend coefficients and gallery reference weights before L2 normalization. An external gallery without explicit provenance has null weights and `gallery_weights_known=false`, never guessed uniform or quality weights.
- `mask`: named components (box/model/dense/dense_fallback/occlusion/glasses/manual) with applied/disabled/missing/failed state and means, the intersection mean, the final face-box mean alpha and the requested strength. Exceptions become fixed non-sensitive failure codes; a landmark exception keeps `dense_mask_failed`, a successful default ellipse is `dense_fallback` applied, and a failed fallback keeps `dense_fallback_failed` while the other masks still apply. Means use different coordinate supports and are not a face-area percentage or identity score (`tests/test_dense_mask_fallback.py`).

Diagnostics contain no image data, embedding, source/model path or raw exception. They are advisory: SWAPPED still means the visible pixel-change check passed, not that identity or naturalness was verified. Records without diagnostics stay explicitly unknown; history is read-only in the [task history dialog](./task-history.md).

## Single-image mapping identity boundary

`detect-faces` returns image-local face indices and a nullable `global_person_id`; null does not identify a person. Automatic pairing in `swapPayload.ts` needs every target analyzed and every face to have a valid positive integer global identity; unknown IDs cannot be grouped or omitted to infer a target. A single photo can instead be bound explicitly by displayed local indices. Quick and batch requests validate positive integer person/reference indices, and the mapping dialog excludes malformed saved bindings so users can replace them without losing images. Regression: `swapPayload.test.ts`, `MultiPersonMatrixModal.test.tsx`.

Source galleries keep face geometry, embeddings and quality/pose metadata after analysis and release decoded images before the next source loads; optional batch AI evidence is frozen losslessly to disk and passed through controlled artifact URLs (`tests/test_reference_disk_lifetime.py`).

### 回贴内存范围

生产回贴使用 `core/compositing.py::warp_face_region` 限定到对齐裁剪在原画布上的投影范围，保留双线性插值外缘并裁到画布内，再用同一 float32 融合公式更新该区域；不会缩小整幅图。原点平移可能引入 OpenCV 的少量插值取整差异，不能宣称与全画布 warp 完全相同。测试见 `tests/test_compositing_region.py`；内存实测与适用限制见 [内存调度](memory-scheduling.md#精修共享队列缩略图与局部回贴)。

## 模型与许可

- 生产默认与 `auto` 路由只用 `inswapper_128`。其权重在 FaceFusion 元数据中为 **Non-Commercial**，分发或商用前须另行取得授权。
- `hyperswap_1c_256`（ResearchRAIL）仅在显式请求并 `allow_research_models` 时使用，不随便携包分发；`ghost_1_256` 为 Apache-2.0，仅限显式实验。
- 评估用的 HyperSwap 1a/1b、AlphaFace、Ghost 2/3 以及外部扩散模型只存在于 `scripts/` 的离线评估工具中，API、CLI 和便携包都不会加载。
- 身份相似度由 ArcFace 余弦衡量，用 `scripts/evaluate_identity_similarity.py` 复现；评测是“身份迁移”，不是自然度验收。
- 未换脸时状态为 `no_visible_change`，常见原因是纯侧脸、手遮脸、戴帽、低头导致掩码覆盖率过低；这是守卫在工作，不是缺陷。

选择这些默认值的依据和被否定的方案见决策笔记：[默认模型与许可](../.agents/notes/swap/20261003-default-swap-model-and-licence.md)、[强度与跳过原因](../.agents/notes/swap/20261003-identity-strength-and-skip-causes.md)、[不采用扩散换脸](../.agents/notes/swap/20261003-reject-diffusion-swappers.md)。

## 证据

手动真实图对比写在 `outputs/diagnostics/facefusion-parity/`。单元覆盖：[`tests/test_facefusion_pipeline.py`](../tests/test_facefusion_pipeline.py)（掩码柔化、pixel boost 往返、极端姿态拒绝、映射结果）、[`tests/test_swapper_policy.py`](../tests/test_swapper_policy.py)（路由）、[`tests/test_task_swap_outcome.py`](../tests/test_task_swap_outcome.py)（部分结果提示）；M01 姿态扩展的校验和与 SCRFD/ArcFace 测量见 [`data/portrait_expansions/README.md`](../data/portrait_expansions/README.md)。实现 [`core/pipeline.py`](../core/pipeline.py)；文档规则 [documentation-governance.md](./documentation-governance.md)。
