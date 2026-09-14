# PersonaWeave model assets

This directory is a **local model cache**, not a source-controlled asset directory. Large ONNX weights and archives are intentionally ignored by Git.

## Setup

From the repository root, run:

```bash
uv run python scripts/download_models.py
```

The downloader creates the following runtime layout:

```text
models/
├── buffalo_l/
│   ├── det_10g.onnx          # required: face detection
│   ├── w600k_r50.onnx        # required: face recognition
│   └── 2d106det.onnx         # required asset; dense landmarks degrade per face on inference failure
├── inswapper_128.onnx        # required: face swapping
├── xseg_1.onnx               # optional enhancement: occlusion mask (hands/props)
├── bisenet_resnet_34.onnx    # optional enhancement: semantic region mask (e.g. glasses)
├── hyperswap_1c_256.onnx     # optional ResearchRAIL evaluation model
├── ghost_1_256.onnx          # optional Apache-2.0 evaluation model
├── crossface_ghost.onnx      # Ghost embedding converter
└── codeformer.onnx           # optional enhancement: masked post-swap detail restoration
```

Other files bundled in `buffalo_l.zip` may also be present. The script keeps existing valid files and downloads missing assets.

`gfpgan_1.4.onnx` may remain in an older local cache but stays an unmanaged retired asset: the current downloader, startup preflight and production pipeline do not require or load it. Unlike CodeFormer (see below), GFPGAN has no fidelity/identity-preservation control, so it was not brought back in the masked-restoration reintroduction and must not be wired into the pipeline without one.

## Managed download integrity

`scripts/download_models.py` verifies these SHA-256 values after download and when deciding whether a local same-name file is reusable:

| Asset | SHA-256 |
| --- | --- |
| `buffalo_l.zip` | `80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f` |
| `buffalo_l/det_10g.onnx` | `5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91` |
| `buffalo_l/w600k_r50.onnx` | `4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43` |
| `buffalo_l/2d106det.onnx` | `f001b856447c413801ef5c42091ed0cd516fcd21f2d6b79635b1e733a7109dbf` |
| `inswapper_128.onnx` | `e4a3f08c753cb72d04e10aa0f7dbe3deebbf39567d4ead6dce08e98aa49e16af` |
| `xseg_1.onnx` | `c4d1498b8a03b5fe2a3a5d2ef2a0402ab03bd51edaf5b2d8d5fb764702a97dd3` |
| `bisenet_resnet_34.onnx` | `4a0b8c958a3c938913bd06a8365dbb3c8761afba6ecbf0d14b3b1f77eb230c96` |

`codeformer.onnx` is loaded conditionally (see below) and is not yet part of the default downloader's pinned set; if you add it to `scripts/download_models.py`, pin the SHA-256 of the exact asset you fetch rather than reusing the value documented here, which only reflects the locally cached file evaluated for this reintroduction (`21710e7ab61c82683576c428e9c1b6fe1ed419586b7b39e394c3449c294b550f`).

## Optional evaluation model

`hyperswap_1c_256.onnx` is supported by the pluggable adapter for controlled research evaluation only. It is not part of the default downloader because FaceFusion labels the weight **ResearchRAIL**. The evaluated asset came from `facefusion/models-3.3.0` and must match SHA-256 `5528c2d76fe9986c99d829278987ef9f3a630cb606db7628d02b57b330f406a5`. Do not redistribute or enable it for commercial workloads without completing a license review.

`ghost_1_256.onnx` and `crossface_ghost.onnx` form the optional Ghost evaluation backend. FaceFusion labels Ghost **Apache-2.0**; the local evaluated files have SHA-256 `4eac28021ae80128e9262080cf0da850e3190e41862650c9e805f9a516b3e924` and `9ec5862d9ff1f723a7380ea89baf87cddec9c56670d4db766702657939284957`, respectively. Keep the converter paired with the model and still review upstream notices before redistribution.

`bisenet_resnet_34.onnx` is a 19-class CelebAMask-HQ face-parsing model (`core/region_parser.py`) used to protect specific semantic regions — currently eyeglasses — that XSeg does not treat as "occlusion" and that InSwapper commonly distorts. FaceFusion attributes it to vendor **yakhyo**, **MIT** license; the evaluated asset came from `facefusion/models-3.0.0` and must match SHA-256 `4a0b8c958a3c938913bd06a8365dbb3c8761afba6ecbf0d14b3b1f77eb230c96`.

## Masked post-swap restoration model

`codeformer.onnx` (`core/restorer.py`) is an **opt-in** (`enable_face_restoration=false` by default) production capability, not a diagnostic script. It restores skin-texture and edge detail lost to the swap models' 256x256 working-crop cap (see `swap_single_face_facefusion` in `core/pipeline.py`). This project previously shipped full-face CodeFormer/GFPGAN restoration in 0.1.0 and retired it because it could alter identity and protected pixels outside the swap's own mask (see `CHANGELOG.md`, `docs/engineering-architecture.md`). The current reintroduction is deliberately scoped to avoid that failure mode:

- restoration output is blended back only through `crop_mask` — the same dense-landmark/occlusion/region-protection mask that already gates the swap itself, so it can never touch background, occluded, or region-protected pixels;
- `restoration_fidelity_weight` (default `0.75`) uses CodeFormer's own identity-preservation control to bias toward the already-swapped face over generative hallucination;
- `restoration_strength` (default `0.5`) is an additional blend dial between the raw swap output and the restored one.

GFPGAN has no equivalent fidelity control, so it was not brought back; do not wire `gfpgan_1.4.onnx` into the pipeline without adding one, per the note above.

## Version-control policy

- Do not force-add `*.onnx`, TensorRT engines, archives, or other generated model binaries to regular Git.
- Update `scripts/download_models.py` when a runtime model name or upstream source changes.
- For reproducible releases, publish a versioned model bundle outside the source repository and add verified SHA-256 checksums before production deployment.
- Review and comply with each upstream model's license and usage restrictions before redistribution or commercial use.
