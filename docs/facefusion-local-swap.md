# FaceFusion local swap parity

PersonaWeave's production local swap path follows the processing sequence used by FaceFusion at commit `4b1dedb853e4838ca7f3cf70b572be241aee2497` (https://github.com/facefusion/facefusion):

1. canonical five-point alignment using the model template. The forward warp's interpolation is chosen from the transform's actual scale factor (`INTER_AREA` when shrinking, `INTER_CUBIC` when the pixel-boost destination enlarges the source content — `INTER_AREA` degrades to near-`INTER_NEAREST` on enlargement per OpenCV's own documented behavior, so it is not used unconditionally);
2. FaceFusion-style pixel boost for target faces larger than the native model input;
3. model inference on aligned tiles and exact interleaved reassembly;
4. mask intersection: soft box mask, strict 106-point anatomical mask when available, XSeg occlusion mask, and a BiSeNet semantic region-protection mask (feather radius on both the XSeg and BiSeNet masks scales with the crop's actual resolution, matching the box mask's own scaling, instead of a fixed pixel sigma). The BiSeNet mask (`core/region_parser.py`, `models/bisenet_resnet_34.onnx`, 19-class CelebAMask-HQ parsing) protects the `glasses` class by default — XSeg only detects foreign-object occlusion (hands, props) and has no notion of "this pixel is an eyeglass lens/frame," so glasses routinely warp or melt during a swap with nothing to stop it;
5. boundary-ring color correction: a full-crop Reinhard LAB harmonization is computed against the original (unswapped) aligned crop, but only its narrow mask-edge transition ring is blended into the swapped result — the swapped face's own interior tone is never touched. This targets exposure/white-balance seams between the source identity photo and the target scene without the whole-face color/identity drift a full-face transfer risks. Disabled when `color_transfer_mode="off"`;
6. inverse affine alpha paste-back, with manual protection subtracted last.

The production path does not use Poisson cloning, post-swap restoration (CodeFormer/GFPGAN), whole-face color transfer, or heuristic hair protection. The former `swap_single_face_seamless()` diagnostic variant is retired and these operations are not supported alternatives: they can alter facial structure, identity, or protected pixels. Ghost remains explicit-only; automatic routing chooses InSwapper unless the user opts into a suitable HyperSwap ResearchRAIL path.

## BiSeNet model provenance

`models/bisenet_resnet_34.onnx` previously held a 15-byte `Entry not found` text stub (a failed-download artifact, not a valid ONNX asset) and was absent from `scripts/download_models.py`'s `MODEL_URLS`. The filename matches FaceFusion's own model exactly: vendor yakhyo, MIT license, hosted at `facefusion/models-3.0.0` on Hugging Face. It has been re-downloaded (SHA-256 `4a0b8c958a3c938913bd06a8365dbb3c8761afba6ecbf0d14b3b1f77eb230c96`, matching the CDN's `x-linked-etag`) and added to the default download set. Its I/O contract was confirmed both from FaceFusion's `face_masker.py`/`choices.py` source and by running real inference against `tests/fixtures/source-01.png`: input tensor `input` at 512×512 (BGR→RGB, ImageNet-normalized, NCHW), output `[0][0]` a `(19, 512, 512)` logit map matching the standard CelebAMask-HQ class order (0 background, 1 skin, 2/3 eyebrows, 4/5 eyes, 6 glasses, 7/8 ears, 9 earring, 10 nose, 11 mouth, 12/13 lips, 14 neck, 15 necklace, 16 cloth, 17 hair, 18 hat). `models/face_parser.onnx` was the other placeholder found alongside it; it has no counterpart anywhere in FaceFusion's model registry and was deleted rather than replaced.

## Multi-person mapping assignment

When a batch request supplies `person_mappings` for a group photo, `resolve_person_mapping_assignment()` (`core/pipeline.py`) solves a globally optimal one-to-one assignment between mappings and detected faces via the Hungarian algorithm (`scipy.optimize.linear_sum_assignment`) over the full cosine-similarity matrix, rather than letting each mapping greedily claim its own best-similarity face in list order. Below-threshold pairs are masked out before optimizing and re-verified against the real similarity afterward, so a pair the algorithm was forced into by a saturated row/column is still rejected correctly.

## Reference quality

Uploaded references are scored from effective face size, crop sharpness, detector confidence, and pose. Gallery embeddings are weighted by this score, and best-match selection uses 70% quality and 30% pose compatibility.

The portrait-library UI deterministically preselects at most eight non-contact-sheet assets, prioritizing a frontal view, the strongest available left/right profiles, left/right chin-down profiles, and near-30° left/right views before filling any remaining slots. It does not randomly choose six images. The executable policy is `selectDiversePortraitAssets()` in `web/src/utils/portraitAssetSelection.ts`.

For a target with absolute five-point yaw ratio above `0.75`, local swapping is skipped unless at least one source reference is within `0.45` yaw-ratio units. A 2D five-point swapper cannot reliably synthesize the hidden half of an extreme profile; bypassing this guard produced doubled eyes and distorted noses. The skip is reported as `extreme_pose_without_matching_reference`, and batch/sync responses preserve successful people while surfacing an actionable partial-result warning.

`identity_blend_strength` controls the visible source-identity contribution on the InSwapper production path. Its API range is `0.4–0.95` and its default is `0.72`. Raising it can make a small face look more changed, but it does not create missing pose evidence and cannot bypass the extreme-profile guard.

## Regression fixture

The manual real-image comparison is written under `outputs/diagnostics/facefusion-parity/`. Unit coverage for mask softness, pixel-boost round trips, extreme-pose rejection, and mapping outcomes lives in [`tests/test_facefusion_pipeline.py`](../tests/test_facefusion_pipeline.py). Model routing is covered by [`tests/test_swapper_policy.py`](../tests/test_swapper_policy.py), partial-result messages by [`tests/test_task_swap_outcome.py`](../tests/test_task_swap_outcome.py), and the generated M01 pose expansion records checksums plus SCRFD/ArcFace measurements in [`data/portrait_expansions/README.md`](../data/portrait_expansions/README.md).

The production implementation referenced by this document is [`core/pipeline.py`](../core/pipeline.py). Documentation authority and synchronization rules are defined in [documentation-governance.md](./documentation-governance.md).
