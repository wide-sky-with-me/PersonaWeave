# Model-native preview workflow validation — 2026-08-13

Scope: bounded-loop implementation, non-billable contract tests, and an
explicitly approved real-photo Yunwu run.

## Active strategy

- `PERSONAWEAVE_PREVIEW_STRATEGY=model_native`
- planner and reviewer: `gpt-5.6-sol`
- image editor: `gpt-image-2`

The default request flow is:

```text
source + optional reference + user instruction
→ freeze user instruction as an immutable requirement
→ GPT-5.6 writes one complete whole-image edit prompt
→ GPT Image 2 edits the original source without a local mask
→ GPT-5.6 scores original/reference/candidate against fixed thresholds
→ pass and stop, or GPT-5.6 writes the next complete prompt
→ GPT Image 2 edits the original source again
→ browser result marked passed or needs review at the configured limit
```

The ComfyUI workflow contract test asserts that model-native generation contains
`LoadImage`, `PersonaWeaveYunwuImageEdit`, and `SaveImage`, with no mask
input. GPT Image 2 uses a 1536-by-1024 landscape provider canvas for the current
3:2 activity photos.

The next candidate is always generated from the original source. The rejected
candidate is supplied only to the reviewer for diagnosis. The planner runs once;
the reviewer owns subsequent complete prompt revisions. The current default is
three rounds and requests may select between one and ten; review outages,
provider errors, and incomplete reviewer prompts stop without a retry.

## Verification

- 39 project tests passed after the requirement-anchored loop refactor;
- frontend JavaScript syntax passed;
- runtime settings reported `model_native`, `gpt-image-2`, and `gpt-5.6-sol`;
- ComfyUI reported `image`, `prompt`, and `model` as required node inputs while
  `mask` and `reference_image` are optional;
- ComfyUI, the local gateway, and the application were healthy after restart.

## Real-photo execution

Input: `data/images/DSC_6193.JPG`, with an instruction to replace both visible
people with the same fictional East Asian woman while preserving hair, glasses,
expressions, pose, certificate, text, background, composition, and lighting.

The first potentially billable attempt used an unqualified 3072-by-2048 canvas.
Yunwu disconnected without sending a response after roughly four minutes. The
workflow returned HTTP 502 and did not review or retry. Model-native generation
was then restored to the previously qualified 1536-by-1024 canvas.

One final request was forced to `max_iterations=1` to bound cost. It returned:

- HTTP 200, PNG RGB, 1536 by 1024, 2,040,357 bytes;
- output SHA-256
  `c9aaf89b7e8273b35f06d30f186ede9380a084d11b865982abe312e58111a7a1`;
- saved result:
  `.runtime/preview-results/DSC_6193_model-native-loop.png`;
- planner count: two visible people and two intended face edits;
- automatic review: failed with confidence 0.98 and marked human review.

The result makes both faces visibly similar and has no obvious face-edge
breakage. It does not meet product acceptance: it regenerates substantial
whole-image detail, changes the certificate mark from `APEX` to an `APLX`-like
shape, changes facial expressions, and alters people/body detail. The original
review also called the requested same-identity similarity an identity mixup.
The reviewer now receives the requested edit context so that intended shared
identity is not treated as a defect; this correction is contract-tested but was
not followed by another paid request.

## Discarded region-protection experiment

An offline experiment used GPT-5.6-proposed face regions to composite a saved
candidate over the original. It achieved zero pixel changes outside the regions,
but it changed the product semantics from the requested model-native iterative
workflow and made model-proposed regions an implicit hard authorization boundary.
That experiment is not part of the active default path. Its saved artifact may
remain as historical evidence, but no runtime claim should describe it as the
current model-native strategy.

## Evidence boundary

This proves the bounded orchestration, provider transport at the qualified
canvas, result reporting, and review-stop behavior. The refactored prompt loop
is covered by local simulated contract tests only; no new paid request has yet
validated its visual quality. Provider billing records are the authority for
actual charges; this validation only records calls attempted.
