# Real-photo deterministic mask demo - 2026-08-19

## Scope

One authorized, potentially billable edit was run against source `I04`. The
requested subject was `P05`, the adult man at the upper right wearing thin
round glasses and a blue denim shirt. The prompt requested a different
fictional adult identity while protecting the other four people, glasses,
hair, lollipop, clothing, pose, composition and scene.

This is an experiment, not a production acceptance result. No independent
target reference was supplied and no licensed identity embedding evaluator was
available locally.

## Runtime evidence

- Generator: `doubao-seedream-5-0-pro-260628`
- API mode: `json_generations`
- Output canvas: 3808 by 5712
- Generation time: 60.906 seconds
- Candidate SHA-256:
  `3eb9cfdef22c41d4eaf79933a2bc5959dffa12df30b6457f2c96c73c8339761b`
- Detected face box: `(2668, 1685, 720, 1068)`
- Local report: `.runtime/demo/report.json`
- Full comparison: `.runtime/demo/comparison-full.jpg`
- Face comparison: `.runtime/demo/comparison-face.jpg`

## Results

The model-native preview changed the correct person and produced a visibly
different mature identity. It broadly retained the gaze, glasses, lollipop,
denim clothing and the other four people's visible identities. It also changed
the protected hairstyle and regenerated almost the entire canvas.

| Gate | Model-native preview | Strict masked result |
| --- | ---: | ---: |
| Local validation | failed | passed |
| Changed pixels inside authorization mask | 387,756 | 387,743 |
| Changed pixels outside authorization mask | 21,214,693 | 0 |
| Outside-mask change ratio | 99.30363808% | 0% |
| Meaningful edit observed | yes | yes |

The strict result restores the original outside a conservative face ellipse.
This proves exact protection of every other person and scene pixel. It does not
prove that the face boundary is perceptually ideal: preserving the original
hair while inserting a model-generated mature face leaves a less natural
hairline transition than the unconstrained preview.

### Blending follow-up

The first strict composite blurred the full authorization ellipse and then
hard-clipped it. Alpha was still about 50% immediately inside the formal edge,
which created a visible oval seam against the restored original pixels.

The second composite now:

- shrinks the opaque blend core by 2.75 feather radii before Gaussian blur, so
  alpha approaches zero before the hard authorization boundary;
- estimates a bounded color offset from bilateral cheek/lower-face samples;
- applied RGB offsets of `(+9.937, +15.719, +15.556)` for this image;
- reapplies the hard authorization mask after blending, retaining zero changed
  pixels outside the formal region.

The v2 lower-face and neck transition is visibly closer and the ellipse seam is
reduced. A faint forehead/hairline ghost remains because the model-native
candidate replaced the hairstyle while strict mode restores it. More feathering
would mix the original identity back into the generated face. The appropriate
next step is provider-native masked generation plus dense face/hair/occlusion
segmentation, not a wider rectangular or elliptical blend.

The four-way comparison is saved at
`.runtime/demo/comparison-blending.jpg`.

### Provider-native face-only mask follow-up

A second, potentially billable `gpt-image-2` request used the provider's native
multipart mask input. Its source-resolution polygon started below the original
fringe and excluded hair, ears and neck. The saved provider candidate is
visibly better at the hair boundary: it does not create the second swept-back
hairstyle seen in the whole-image candidate.

The first recovery attempt exposed an experimental workflow wiring error: the
final `SaveImage` node consumed the boolean output of the pixel guard instead
of the protected image. The paid candidate was already saved by the preceding
node, so it was recovered locally without another provider request. The graph
now saves the image output of `PersonaWeaveMaskedOriginalComposite`.

The shared composite node was also corrected. A normal Gaussian mask remains
about 50% opaque at the formal boundary, which produces a clipped seam when the
outside is restored. It now remaps that midpoint to zero, making feathering
inward-only while retaining the exact authorization envelope.

Recovered evidence:

- authorization-mask pixels: 315,486;
- changed pixels inside the mask: 305,455 (96.820461%);
- changed pixels outside the mask: 0;
- comparison: `.runtime/demo/comparison-native-masked.jpg`.

This result still fails product acceptance. Hair preservation and boundary
quality improved substantially, but the requested approximately 35-year-old,
square-jawed identity was not produced strongly enough. Pixel change proves an
edit occurred; it does not prove that the requested identity was achieved.

### Container runtime follow-up

The experiment now packages ComfyUI in `infra/comfyui/Dockerfile` and exposes it
through the Compose `local-gpu` profile. The image pins the ComfyUI commit and
CUDA PyTorch index, verifies the MediaPipe model digest, and embeds the
PersonaWeave node pack. Docker-managed volumes own models, inputs and outputs.

The gateway can instead target a main-branch or hosted base service by omitting
the profile and setting `PERSONAWEAVE_COMFY_BACKEND_URL`. This removes the
project's dependency on directly launched WSL processes while preserving the
same provider contract.

## Conclusion

The current model-native generator cannot guarantee unattended formal quality.
Prompt compliance is good enough for rapid preview, but an apparently stable
whole image still changed 99.3% of pixels outside the authorized face region.
The deterministic mask gate prevents that candidate from being falsely
accepted, and strict compositing makes the protection claim mechanically true.

Automatic formal acceptance still requires the next roadmap stage: dense face
landmarks/segmentation for natural boundaries, a licensed identity embedding
provider, protected-person identity checks, pose/expression/occlusion checks,
and cross-result consistency evidence. Until those are calibrated, the strict
result must remain `needs_review` even when its pixel gate passes.

## Reproduction

```powershell
uv run python scripts\evaluate_masked_edit_demo.py `
  --source <authorized-source> `
  --candidate <model-native-candidate> `
  --bbox 2668,1685,720,1068 `
  --output-dir .runtime\demo `
  --generation-seconds 60.906
```
