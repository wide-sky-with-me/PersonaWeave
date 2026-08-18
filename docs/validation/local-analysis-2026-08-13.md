# Local face-analysis validation — 2026-08-13

Scope: four user-authorized images selected from `data/images/`. All images are
5568 by 3712 pixels. The test used local ComfyUI on the GTX 1650 and the pinned
Comfy-Org MediaPipe model; no external inference or billable image generation
was called.

| Image | Coverage | Whole-image baseline | Tiled v2 | Review finding |
| --- | --- | ---: | ---: | --- |
| `DSC_6193.JPG` | close two-person portrait | 3 | 2 | Baseline falsely detected a framed wall pattern; v2 removed it |
| `DSC_6204.JPG` | medium-distance subject and distant background people | 1 | 5 | Tiling recovered small background faces |
| `DSC_6206.JPG` | distant group | 0 | 6 | Tiling repaired the baseline's complete miss |
| `DSC_6217.JPG` | foreground backs and distant background people | 0 | 6 | Foreground backs were not guessed; visible background faces were detected |

The v2 workflow uses 1024-pixel tiles with 25% overlap, a 0.65 detector
threshold, source-coordinate remapping, and IoU NMS. Every observation remains
`needs_confirmation=true`. These four samples validate the execution path and
show a clear recall improvement; they are not a sufficient accuracy benchmark
for production acceptance or cross-image identity consistency.
