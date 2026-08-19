# PersonaWeave

PersonaWeave is a portable orchestration service for consent-based batch face
replacement. It keeps identity mapping, human confirmation, retries, and audit
state outside the inference provider. No model needs to run on the application
host.

The current milestone implements the durable product workflow, a React
workbench, remote execution, retry/progress tracking, local tiled face analysis,
contract-scoped formal batch replacement, canonical semantic authorization
masks with outside-mask source restoration, and independent dual-review
validation. It also retains a guarded single-image replacement preview for
advisory iteration outside the formal delivery path.

## Run locally

```bash
uv sync
cp .env.example .env
uv run uvicorn personaweave.main:app --reload
```

The repository-level `uv.toml` keeps uv's cache in the ignored `.uv-cache/`
directory. This avoids failures in sandboxed environments where the default
`~/.cache/uv` location is mounted read-only.

Open:

- `http://127.0.0.1:8000/app/` for the minimal workflow console;
- `http://127.0.0.1:8000/docs` for the API.

The workbench is a React + TypeScript application under `frontend/`, built with
Ant Design and Motion. Its production bundle is served by FastAPI from
`src/personaweave/static/`. After frontend changes, rebuild it with:

```bash
cd frontend
pnpm install
pnpm build
```

## Local ComfyUI execution

The GTX 1650 development path uses a pinned ComfyUI checkout and a separate uv
environment under the ignored `.runtime/` directory:

```bash
bash scripts/setup_local_comfy.sh
bash scripts/run_local_comfy.sh
```

See `docs/local-comfy.md` for the port, GPU compatibility flags, model-license
gate, Yunwu masked image-edit boundary, and readiness probe.

Run the local UI stack in three terminals:

```bash
bash scripts/run_local_comfy.sh
bash scripts/run_local_comfy_gateway.sh
bash scripts/run_local_app.sh
```

Open `http://127.0.0.1:8100/app/` and use **快速编辑**. The source image is
always required. Add a reference photo to replace all detected faces with that
identity, or omit it and describe one consistent fictional identity in text.
Each click may incur a Yunwu image-edit charge and sends the selected images to
that external provider.

## Workflow

```text
draft
  -> analyzing
  -> awaiting_confirmation
  -> ready
  -> swapping
  -> validating
  -> review
  -> completed
```

Formal batches stop at `awaiting_confirmation` until their immutable task is
compiled with `POST /api/v1/batches/{id}/task/compile` and the returned
SHA-256 is explicitly confirmed with
`POST /api/v1/batches/{id}/task/confirm`. Compilation snapshots the complete
person mapping, per-person editable/protected head attributes, referenced
target profiles, source-image lineage, and a bounded reference-coverage
assessment for every source-person occurrence. For each source containing at
least one editable occurrence, it obtains one bounded semantic-mask proposal
and validates it locally against the confirmed faces and permissions. A
KEEP-only source has no mask and creates no swap job. Each resulting
source-resolution binary grayscale PNG is the canonical authorization mask.
The immutable source evidence records the exact editable face-instance IDs;
that single projection determines whether a source requires a mask and is the
same projection used to create formal jobs. Compilation validates and returns
the canonical PNG bytes without writing them to the filesystem. The repository
then validates and atomically persists those bytes with their database rows,
and owns invalidation and deletion cleanup. Persisted mask locations are
internal runtime state: no storage URI appears in contract evidence or its
hash.

Identity edits always authorize both `face_identity` and `face_features`,
because visible facial geometry carries identity; they do not implicitly
authorize hair, pose, expression, gaze, body, or scene changes. Other head
attributes remain protected unless the immutable task explicitly requests them.

Semantic include contours and contained exclusion holes replace the former
formal bounding-box ellipse raster. Before local safety checks and persistence,
the raster is intersected with the union of the confirmed editable face boxes;
proposal geometry can refine that scope but cannot authorize pixels beyond it.
Self-intersecting or protected-face-overlapping geometry fails compilation
rather than falling back to a coarse mask.
The proposal compiler receives each confirmed face box in both exact source
pixels and normalized `[0,1]` coordinates, together with the source dimensions.
Every editable face's include geometry must cover that face-box center; a
contained exclusion may still remove protected features at the center after
this authorization check.
If validation marks a canonical mask `mask_uncertain`, confirmation must send
that mask's exact digest in `acknowledged_mask_sha256s`; the reason remains in
result and validation evidence and keeps delivery review-only. Public contract
evidence exposes only mask digest, dimensions, status, review reasons, and a
current-contract `preview_url`; that URL serves the PNG only while its digest
still belongs to the batch's current compiled contract. Formal generation is
restricted to `multipart_edits`, including retries.

Each source occurrence is assessed against only its selected target identity's
views using face size, pose, lighting, occlusion, and blur context; missing or
uncertain coverage fails closed as requiring manual review but does not prevent
generation. A swap that carries this immutable review requirement finishes in
`needs_review` even when generation QA passes, with the applicable review
reasons in its job output. `POST /api/v1/batches/{id}/task/run` atomically
claims only the current confirmed contract and creates its initial source jobs;
duplicate, concurrent, or stale starts cannot create jobs or re-open the batch.
Batch detail `latest_jobs` and `/progress` expose only the latest attempts
belonging to that current contract for swap and validation; superseded contract
failures remain historical records but do not reappear as active work or counts.
Changing the task, sources, references, identities, person topology, or mappings
invalidates the prior confirmation. Numbered target identities and reference
photos improve identity consistency but remain optional for text-only edits.
**快速编辑** keeps its separate model-native preview behavior.

## Remote providers

- The configured Yunwu visual model acts as an OpenAI-compatible multimodal
  planner and inspection/QA provider. In model-native preview it plans the
  whole-image edit before the independently configured image model runs, then
  compares source and result. A failed
  advisory check can feed a bounded next iteration, while a passing check stops
  early. Each iteration regenerates from the original source; review outages and
  provider errors stop instead of retrying. The default maximum is three rounds,
  and the request may select between one and ten rounds.
  It is not treated as a biometric identity oracle.
- Local tiled MediaPipe detection is active. Durable clustering and production
  face replacement continue to use the provider-neutral inference gateway
  contract in `docs/provider-contract.md`.
- A hosted ComfyUI instance can sit behind that gateway. This prevents its node
  names and workflow JSON from leaking into the product domain.
- The included `personaweave-comfy-gateway` process implements that adapter and
  supports ComfyUI upload, prompt submission, history polling, output download,
  authentication, and durable completed-result idempotency.

## Configuration status

`GET /api/v1/health` reports provider readiness plus the configured model and
protocol, but never returns credentials. `GET /api/v1/providers/yunwu/models`
performs a safe catalog probe and returns Yunwu's live
`supported_endpoint_types` metadata instead of guessing capabilities from model
names.

Run `uv run python scripts/probe_yunwu.py` to send two generated, non-personal
test images through the configured visual-QA model. The probe validates the
response contract and never prints the API key or raw model response.

Visual QA uses an explicit profile: `PERSONAWEAVE_YUNWU_VISION_MODEL`,
`PERSONAWEAVE_YUNWU_VISION_API_MODE`, and
`PERSONAWEAVE_YUNWU_VISION_API_PATH`. Formal final validation requires two
independent Yunwu reviewer profiles: `PERSONAWEAVE_YUNWU_REVIEWER_MODEL_A` and
`PERSONAWEAVE_YUNWU_REVIEWER_MODEL_B`. Configure both with distinct model IDs;
configuring only one or reusing the same ID is rejected. Their verdicts are
retained separately, and a result is eligible for human batch approval only
when both reviewers and the local source comparison pass. These reviewers share
the visual API mode, path, and multimodal timeout above.

Model-native preview image editing has an independent profile:
`PERSONAWEAVE_YUNWU_IMAGE_MODEL`, `PERSONAWEAVE_YUNWU_IMAGE_API_MODE`,
`PERSONAWEAVE_YUNWU_IMAGE_API_PATH`, `PERSONAWEAVE_YUNWU_IMAGE_SIZE`, and
`PERSONAWEAVE_YUNWU_IMAGE_RESPONSE_FORMAT`. Formal batches use the separate
`PERSONAWEAVE_FORMAL_IMAGE_*` profile, which is restricted to
`multipart_edits` because its confirmed authorization mask must reach the
image-edit request. Its response format may be left blank for endpoints such
as Yunwu GPT Image that reject that optional field and return `b64_json` by
default. `json_generations` remains preview-only. Changing a model never
silently changes its endpoint or payload contract.

The default `PERSONAWEAVE_PREVIEW_STRATEGY=model_native` path does not use local
face detection or a local mask. The configured visual model first converts the
immutable user requirement into a complete edit instruction, the configured
image model edits the original, and the visual model scores the candidate
against the original. A failed review must
produce a complete next-round instruction that retains the original requirement;
the image model always starts again from the original source. The non-billable
face-plan preview remains under advanced diagnostics.

Formal batch generation uses the validated canonical semantic mask for both
provider conditioning and the authoritative local post-composite. The configured
image model may return a complete edited canvas, but only pixels authorized by
the source-resolution mask are retained; every other pixel is restored directly
from the source. Provider multipart mask bytes may be resized or inverted for
that API's convention and therefore are not the canonical PNG or digest.

This formal mask is deliberately distinct from the normalized face-oval/ellipse
used by the optional controlled-mask preview nodes. Those preview masks are
diagnostic UI artifacts and are never a substitute for the compiled formal
authorization raster. Formal candidates are also checked for unintended
people/background/text changes, visible edit seams, and global white-balance,
exposure, contrast, saturation, gamma, and color-cast drift. The default
model-native preview remains a full-frame edit without local compositing.

The application does not use Yunwu for biometric identity assignment. The
configured inference gateway provides local face detection and the protected
execution boundary. When no qualified static swap workflow is installed, the
included Comfy gateway constructs a bounded masked multipart graph around the
configured Yunwu formal image profile, then restores canonical-mask-external
pixels locally. Final validation runs in the application as local source
comparison plus two distinct Yunwu reviewer models.

Do not point `PERSONAWEAVE_INFERENCE_BASE_URL` at Yunwu's OpenAI-compatible
`/v1` endpoint. That setting requires a service implementing PersonaWeave's
three face-specific endpoints. `PERSONAWEAVE_YUNWU_BASE_URL` is the separate
setting for visual QA.

## Deterministic batch benchmark

Use the checked-in benchmark harness to summarize an existing 10–20 image
batch from public API evidence:

```bash
uv run python scripts/benchmark_batch.py benchmark-manifest.json \
  --base-url http://127.0.0.1:8000 \
  --output benchmark-report.json
```

The manifest identifies the batch and assigns scenario coverage to each stable
source code:

```json
{
  "batch_id": "batch UUID",
  "cases": [
    {
      "source_asset_code": "I01",
      "scenario_tags": ["frontal", "expression"]
    }
  ]
}
```

Provide 10–20 unique `Ixx` cases whose combined tags cover `frontal`,
`profile`, `occlusion`, `multi_person`, `low_resolution`, `expression`, and
`lighting`. Add `--execute` to run the confirmed task before collecting its
report. The command returns 0 only when every case has a successful or
needs-review final status and usable validation evidence; incomplete or failed
cases return 1, while an invalid manifest or API response returns 2. Reports
contain bounded, sanitized failure and reviewer reasons rather than raw
provider payloads.

## Remote execution and retry

Create stage jobs first, then execute the matching stage:

```text
POST /api/v1/batches/{batch_id}/analysis/jobs
POST /api/v1/batches/{batch_id}/stages/analyze/execute

POST /api/v1/batches/{batch_id}/swap/jobs
POST /api/v1/batches/{batch_id}/stages/swap/execute

POST /api/v1/batches/{batch_id}/stages/validate/execute
```

Failures stay attached to the exact image and stage. Retry only the latest
failed attempt with `POST /api/v1/jobs/{job_id}/retry`; successful images are
not rerun. A retry may refresh only the provider generation profile
(`image_model`, `image_api_mode`, `image_api_path`, `image_output_size`, and
`image_response_format`). The confirmed contract, permissions, reference
evidence, manual-review state, and all other stored job input remain immutable.

Public batch responses expose the user-authored `task_instruction` needed to
review the persisted task, plus bounded compiled evidence. Public identity
responses expose only bounded compiled evidence. Both exclude storage URIs,
raw provider payloads, and internal compiled instruction/provider prose.
Public job responses expose the stable `error_code`, localized safe `error_message`, and
an `error_retryable` boolean derived from the canonical provider policy.
Clients should use `error_retryable` rather than interpreting provider text or
maintaining their own error-code classification.

## Security

- Put credentials in environment variables or a secret manager.
- Never commit `.env`.
- Obtain consent from every affected person and define retention/deletion rules
  before processing real photos.
