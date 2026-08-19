# Local ComfyUI runtime

PersonaWeave uses ComfyUI as a replaceable execution engine. Batch state,
identity mapping, confirmation, retries, and audit history remain in the
PersonaWeave control plane.

## Install and run

```bash
bash scripts/setup_local_comfy.sh
bash scripts/run_local_comfy.sh
# In a second terminal:
bash scripts/run_local_comfy_gateway.sh
```

The pinned checkout and its isolated Python environment live under
`.runtime/ComfyUI`, which is intentionally ignored. The reproducible commit and
PyTorch index are stored in `infra/comfyui/versions.env`. The project-owned
custom nodes are linked from `comfy-nodes/PersonaWeave`. Setup also installs the
5.42 MB Apache-2.0 MediaPipe face-landmarker package published by Comfy-Org and
verifies its pinned SHA-256 before activation.

The local server listens only on `127.0.0.1:8188`. The launch script reserves
0.8 GB VRAM for the desktop and disables CUDA malloc async for GTX 1650
compatibility. Override these values with `PERSONAWEAVE_COMFY_LISTEN_HOST`,
`PERSONAWEAVE_COMFY_LISTEN_PORT`, and
`PERSONAWEAVE_COMFY_RESERVE_VRAM_GB`.

Run `bash scripts/probe_local_comfy.sh` after startup. For a self-hosted server,
the ComfyUI API prefix is empty.

Run `bash scripts/probe_local_mask_guard.sh` to execute a generated local
workflow that changes only a synthetic 16-by-16 region and verifies zero changed
pixels outside the mask. This probe never calls an external model.

Use a consented photo to exercise the real local face detector:

```bash
bash scripts/probe_local_face_analysis.sh /path/to/photo.jpg
```

The active analysis workflow uses overlapping 1024-pixel tiles so small faces
in the project's 5568-by-3712 activity photos are not lost by whole-image
downscaling. Tile detections are mapped back to source coordinates and deduped
with NMS. This outputs bounding boxes only. It deliberately marks every detected face as
`needs_confirmation=true`; face detection alone is not an identity mapping.
The same path can be checked end-to-end through the gateway with:

```bash
bash scripts/probe_local_gateway_analysis.sh /path/to/photo.jpg
```

After explicitly approving a potentially billable provider call, run the
synthetic Yunwu image-edit contract probe with:

```bash
bash scripts/probe_local_yunwu_image_edit.sh
```

It uploads only a generated test card, sends one image-edit request, restores
the original outside a circular mask, and requires zero changed protected
pixels. It does not use project photos.

The gateway listens on `127.0.0.1:8010` by default. Its `/ready` endpoint checks
the active analysis workflows and every node required by the selected formal
generation path. Without `swap.json`, readiness requires the built-in dynamic
masked multipart nodes; without `validate.json`, final validation is reported as
the application-side source comparison plus two independently configured Yunwu
reviewers. Optional static workflows are validated when present.

## Quick replacement preview

Start `bash scripts/run_local_app.sh` and open
`http://127.0.0.1:8100/app/`. The quick preview performs this temporary flow:

```text
source upload
→ freeze the original user requirement
→ GPT-5.6 writes one complete whole-image edit instruction
→ Yunwu GPT Image 2 edits the original without a local face mask
→ GPT-5.6 scores source/result and, when needed, writes the next complete prompt
→ repeat from the original until pass or the configured limit
→ browser preview/download
```

An optional reference photo is submitted as the second image. Without a
reference, the text description defines one fictional identity for every
visible face selected by the model. This preview does not save a batch mapping and does not establish
cross-photo identity consistency. Source and reference images leave the local
machine for Yunwu processing, so the user must explicitly authorize that upload
and the potentially billable request.

Model-native preview delegates subject understanding and edit localization to
the multimodal and image models. It does not run local face detection or use a
local mask. GPT-5.6 plans only once at the start. Every generation receives the
verbatim immutable user requirement plus either the initial plan or the
reviewer's complete revised prompt, and always edits the original source rather
than the rejected candidate. Review uses fixed acceptance thresholds for
instruction fulfillment, identity consistency, face naturalness, source
pose/expression preservation, unrelated source content, and text/logo fidelity.
The user selects between one and ten rounds; the default is three. A passing review stops early;
provider/review errors or a missing complete next prompt stop without another
image-generation charge.

The page previews a selected source image and optional reference before
submission, then shows the generated result with a download link. Batch source
images and identity reference images are also rendered as thumbnails. Browser
previews use local object URLs until upload; selecting a file does not by itself
send it to Yunwu.

The optional **面部区域预览** panel visualizes the locally proposed face area. This
workflow maps tiled MediaPipe landmarks back to source coordinates, removes
duplicate tile detections, builds one 1024-by-1024 contextual crop per face, and
uses the MediaPipe face oval instead of an expanded bounding-box ellipse. The
green overlay is the proposed editable area. It calls only local ComfyUI.

The same check is reproducible from the command line:

```bash
bash scripts/probe_local_face_plan.sh \
  data/images/DSC_6193.JPG \
  .runtime/preview-results/DSC_6193_face-plan.png
```

This optional mask keeps hair, ears, neck, clothing, and background outside
the editable region. Eyeglasses remain inside the face oval; production
replacement still needs an occlusion-restoration layer or a user-corrected mask
before a controlled-mask strategy can promise exact eyewear preservation. It is
not consumed by the default model-native preview.

## Provider boundary

The `PersonaWeaveYunwuMaskedImageEdit` node is an optional candidate generator.
It reads its API key from the process environment, never from workflow JSON, and
expects an OpenAI-compatible multipart `/images/edits` response containing
`data[0].b64_json`. The endpoint must be qualified with a synthetic image before
real photos are sent.

Controlled-mask generation output must pass through:

1. `PersonaWeaveMaskedOriginalComposite`, which restores original pixels outside
   the explicit mask;
2. `PersonaWeaveOutsideMaskPixelGuard`, which fails the job if protected pixels
   changed;
3. normal identity and visual validation in the PersonaWeave gateway.

Model-native generation bypasses those mask-specific nodes, uses GPT-5.6 review,
and must be presented as a whole-image model edit without pixel-level protection.
Neither strategy is a biometric identity verifier.

## Model qualification

Do not run third-party node installers without auditing their automatic model
downloads. ReActor currently downloads InsightFace INSwapper and recognition
weights whose public terms are non-commercial. Its source can be inspected in a
disabled runtime directory, but it is not part of the active workflow.

Every activated model needs a manifest containing source URL, SHA-256, model
license, allowed use, node version, and a small quality benchmark on consented
test assets.
