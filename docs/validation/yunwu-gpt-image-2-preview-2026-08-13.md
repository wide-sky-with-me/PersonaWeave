# Yunwu GPT Image 2 real-photo preview — 2026-08-13

Scope: one user-approved, potentially billable prompt-mode edit of
`data/images/DSC_6193.JPG` using `gpt-image-2`. The prompt requested that both
detected faces become the same fictional East Asian woman while preserving the
source pose, expression, glasses, hair, and lighting.

## Execution result

- HTTP result: 200
- Detected faces: 2
- Provider model: `gpt-image-2`
- Output: PNG, RGB, 5568 by 3712, 22,349,753 bytes
- Output SHA-256:
  `49d4ec9e0c066cae070595c5c02e06e89319c073a8852fdf1a06510909f17e2f`
- Protected pixels checked: 20,144,173
- Protected pixels changed: 0
- Local pixel guard: passed
- Saved result: `.runtime/preview-results/DSC_6193_gpt-image-2.png`

## Visual review

The two outputs read as the same target identity and are visually coherent with
the scene. Background, clothing, pose, certificate, text, framing, and other
protected regions are preserved by the local composite.

The result is not yet a full product-quality pass. The editable ellipses include
parts of the hair and eyewear, and the model normalized both expressions toward
a similar closed-mouth smile. Consequently, hair, glasses shape, and original
expression preservation failed the stricter PersonaWeave acceptance criteria.

The case qualifies the `gpt-image-2` endpoint, response parsing, two-face prompt
mode, full-resolution restoration, and outside-mask safety guard. Further mask
segmentation and prompt/conditioning work is required before claiming
high-fidelity face-only replacement.
