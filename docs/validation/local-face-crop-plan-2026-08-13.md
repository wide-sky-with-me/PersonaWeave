# Local per-face crop and mask validation — 2026-08-13

Scope: `data/images/DSC_6193.JPG`, processed entirely by local ComfyUI. No
provider image-generation endpoint was called and no generation fee was
incurred.

## Result

- detected faces: 2
- source size: 5568 by 3712
- crop size: 1024 by 1024 per face
- crop context scale: 2.2
- mask: MediaPipe `face_oval`
- preview sheet: 2048 by 1024 PNG
- saved preview: `.runtime/preview-results/DSC_6193_face-plan.png`

| Face | Source bbox | Source crop | Face long edge in planned cloud input |
| --- | --- | --- | ---: |
| `face-0` | `(2093, 1097, 374, 399)` | `(1841, 858, 878, 878)` | 465 px |
| `face-1` | `(3114, 1069, 436, 484)` | `(2800, 779, 1065, 1065)` | 465 px |

The previous full-frame provider canvas reduced these faces to roughly 103 and
120 pixels on their long axes. Per-face planning raises both to approximately
465 pixels while preserving contextual hair, lighting, and head pose around the
editable face region.

## Review boundary

The face-oval mask correctly excludes hair, ears, neck, clothing, and
background. It still includes eyeglasses because landmarks describe facial
geometry rather than semantic occlusions. This validates crop geometry and a
substantially tighter base mask, but an eyewear/hair-strand restoration layer or
manual mask correction remains required before a paid high-fidelity comparison.
