# Yunwu Seedream 5.0 Pro bounded full-loop test — 2026-08-16

## Scope

The test used `data/images/DSC_6193.JPG` and requested that the two real people
be changed to the same text-defined 28-year-old East Asian woman while
preserving each person's expression, eyewear, hairstyle, body, clothing, pose,
hands, certificate, scene, text, composition, lighting, color, and sharpness.

The total paid image-generation budget was capped at three calls. The first
attempt used the configured three-round endpoint but stopped safely after its
first generation because GPT-5.6 review exceeded the old 120-second timeout.
It returned `review_unavailable` with `error_type=ReadTimeout`; no blind retry
or second generation occurred. Based on this runtime evidence, the multimodal
timeout default and local configuration were increased to 300 seconds.

The continuation was capped at two generations so that the complete test still
used no more than the original three paid image calls in total.

## Runtime result

- Image model: `doubao-seedream-5-0-pro-260628`
- Reviewer: `gpt-5.6-sol`
- Continuation HTTP result: 200
- Continuation wall time: 326.57 seconds
- Completed continuation iterations: 2 of 2
- Stop reason: `max_iterations_reached`
- Final acceptance: failed / manual review required
- Final output: PNG, RGB, 5568 by 3712, 23,929,020 bytes
- Final SHA-256:
  `09da192f1ea71bf4578774401ee574b5f1afb715e93e0b0cc695d11d4bfd743d`
- Final result:
  `.runtime/preview-results/DSC_6193_seedream-full-iteration-2.png`

## Iteration evidence

| Metric | Iteration 1 | Iteration 2 |
| --- | ---: | ---: |
| Instruction fulfillment | 74 | 78 |
| Target identity consistency | 93 | 88 |
| Face naturalness | 86 | 88 |
| Pose/expression preservation | 70 | 86 |
| Source-content preservation | 87 | 89 |
| Text/logo preservation | 99 | 99 |
| Whole-image RMSE vs source | 0.087284 | 0.0370234 |

The first review correctly identified that the left person's neutral closed
mouth had become a toothy smile and that both short hairstyles were redrawn. It
returned a complete standalone correction prompt, and iteration 2 regenerated
from the immutable original rather than editing iteration 1.

Iteration 2 materially improved the left expression and whole-image
preservation. It still failed because the hair, hairlines, side strands, and
head contours were visibly redrawn. Source-content preservation scored 89,
below the required 90 threshold. The left neutral expression was also slightly
softened. The loop therefore correctly returned a needs-review result instead
of claiming success.

## Conclusion

The bounded plan-generate-review-reprompt-regenerate-review control flow is
runtime-validated. Prompt-only whole-image editing improves after reviewer
feedback but still cannot reliably enforce face-interior-only modification.
Meeting the formal PersonaWeave acceptance criteria requires a localized face
mask/composite or a provider with an explicit edit mask, especially for hair
and head-boundary preservation.
