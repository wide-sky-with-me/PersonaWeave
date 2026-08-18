# Yunwu Seedream 5.0 Pro real-photo preview — 2026-08-14

Scope: one explicitly bounded, potentially billable model-native edit of
`data/images/DSC_6193.JPG`, with `max_iterations=1`. The prompt requested that
the two people become the same text-defined East Asian woman while retaining
their individual expressions, eyewear, hair, clothing, bodies, pose, scene,
certificate, text, and lighting.

## Runtime evidence

- Model: `doubao-seedream-5-0-pro-260628`
- Yunwu endpoint: `POST /api/v3/images/generations`
- Provider response: HTTP 200
- ComfyUI provider execution: 261.40 seconds
- End-to-end request: 367.93 seconds
- Iterations completed: 1 of 1
- Output: PNG, RGB, 5568 by 3712, 23,266,473 bytes
- Output SHA-256:
  `7c3e811b9a01d414c4c20b1a4943c58369a2dd1e35074985092be6ad0a440523`
- Saved result:
  `.runtime/preview-results/DSC_6193_seedream-5-pro.png`
- Whole-image RMSE against the JPEG source: `0.0902619`

## Visual assessment

Both requested faces were changed and read as similar young East Asian women.
The source dimensions, framing, background, certificate, large wall text,
clothing, pose, and hands are visually well preserved. The result is materially
closer to the requested edit than the prior GPT Image 2 experiment.

It is not a PersonaWeave acceptance pass. Both hairstyles were visibly redrawn
despite being protected by the prompt, and visual similarity alone is
insufficient to establish that the two generated faces are the same identity.
The GPT-5.6 result-review call was unavailable, so the API correctly returned
`X-PersonaWeave-Needs-Review: true` and did not start another paid generation.
