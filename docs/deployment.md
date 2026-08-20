# Deployment boundary

PersonaWeave is split into a lightweight control plane and a replaceable remote
inference plane.

## Control plane

The current FastAPI service owns:

- batches, source assets, reference identities, and people;
- human-confirmed keep/replace mappings;
- stage jobs, attempts, error codes, and progress;
- conversion of local assets to data URIs for remote calls;
- output hash checks and local persistence for returned data URIs.

Swap and validation progress is scoped to the batch's currently confirmed
contract. Recompiling and confirming a different contract retains superseded
job attempts for audit, but those attempts do not contribute to stage coverage,
completion, or failed-job reconciliation after a restart.

It does not load a face model or require a GPU.

## Inference plane

Set `PERSONAWEAVE_INFERENCE_BASE_URL` to a service implementing the contract in
`provider-contract.md`. That service may use hosted ComfyUI, a commercial face
API, or another GPU backend.

All calls include:

- `Authorization: Bearer ...` when a gateway key is configured;
- a deterministic `Idempotency-Key` scoped to the batch, stage, and asset.
  Swap keys also include a bounded digest of the behavior-affecting request
  fields, excluding the source asset URI, so identical requests remain stable
  while generation-profile, reference, or fidelity changes receive distinct
  keys;
- `X-PersonaWeave-Workflow-Version: 1`.

For an initial hosted ComfyUI implementation, the gateway should own the native
ComfyUI workflow JSON and translate PersonaWeave requests to `/api/prompt`.
Keeping that translation behind the gateway lets the ComfyUI node pack and
model change without a database migration in PersonaWeave.

That gateway is now included as `personaweave.comfy_gateway`. It uploads input
images, submits API-format prompts, polls `/history/{prompt_id}`, reads the
configured output node, and downloads image outputs through `/view`. Use
`PERSONAWEAVE_COMFY_BACKEND_API_PREFIX=/api` for Comfy Cloud; use an empty value
for a self-hosted server whose routes are exposed without the `/api` prefix.

## Gateway configuration

The minimum variables for the standalone gateway are:

```text
PERSONAWEAVE_COMFY_BACKEND_URL=https://your-comfy-service.example
PERSONAWEAVE_COMFY_BACKEND_API_KEY=provider-secret
PERSONAWEAVE_COMFY_GATEWAY_API_KEY=private-shared-secret
PERSONAWEAVE_COMFY_WORKFLOW_DIR=./comfy-workflows
```

After storing the backend key, run `uv run python scripts/probe_comfy.py`.
The probe calls the live `/object_info` endpoint and prints only the backend URL,
node count, and face-related node-class candidates. It never prints the API key.

Use the same private shared secret as `PERSONAWEAVE_INFERENCE_API_KEY` on the
control plane. The gateway requires `Idempotency-Key`, persists completed
responses in SQLite, and rejects reuse of a key with a different request.

Remote image URLs are rejected unless their HTTPS host appears in
`PERSONAWEAVE_COMFY_ALLOWED_ASSET_HOSTS`. Data URIs are accepted up to the
configured size limit. Redirects are not followed while fetching source assets,
which prevents a permitted public URL from redirecting into a private network.
Reference downloads and uploads are deduplicated per request and share a
gateway-wide concurrency bound. Set
`PERSONAWEAVE_COMFY_MAX_CONCURRENT_TRANSFERS` from `1` through `16` (default
`4`) to match the backend and network capacity.

The workflow binding format and non-runnable examples are documented in
`comfy-workflows/README.md`. Do not activate an example until the exact node
classes and model files are present on the selected ComfyUI backend.
`GET /health` reports configuration without contacting the backend. `GET
/ready` contacts the backend, requires both analysis workflows, and verifies
every node class needed by the active generation path against live
`/object_info`. A missing static `swap.json` selects the guarded dynamic masked
multipart graph. A missing `validate.json` selects application-side source
comparison and dual Yunwu reviewers. Optional static workflows are also
validated when present. Container readiness uses this stricter endpoint.

## Model and license qualification

Do not assume that a ComfyUI node's open-source code license also permits
commercial use of its downloaded weights. In particular, common ReActor setups
download InsightFace `buffalo_l` and `inswapper_128` weights whose upstream
terms restrict the pretrained models to non-commercial research. For activity,
advertising, or customer-photo workloads, use a provider/model with explicit
commercial rights or obtain the appropriate license before activating the
workflow.

Diffusion-based whole-image editing nodes are also not a compliant fallback for
PersonaWeave: even when they preserve composition perceptually, they cannot
provide the required outside-mask pixel protection. A production workflow must
return or retain the authorized face masks and run pixel-difference checks.

## Container deployment

`compose.yaml` runs the control plane and gateway as separate processes from one
portable image. Before starting it, provide:

```text
PERSONAWEAVE_GATEWAY_SHARED_KEY=generate-a-long-random-value
PERSONAWEAVE_COMFY_BACKEND_URL=https://your-comfy-service.example
PERSONAWEAVE_COMFY_BACKEND_API_KEY=provider-secret
PERSONAWEAVE_YUNWU_API_KEY=visual-qa-secret
PERSONAWEAVE_YUNWU_REVIEWER_MODEL_A=first-visual-review-model
PERSONAWEAVE_YUNWU_REVIEWER_MODEL_B=second-distinct-visual-review-model
PERSONAWEAVE_COMFY_WORKFLOW_DIR=/absolute/path/to/validated/workflows
```

Then run `docker compose up --build`. The default stack starts only the
PersonaWeave control plane and serves the workbench at
`http://127.0.0.1:8100/app/`; it does not start WSL or a local inference
process. SQLite databases and generated assets are stored in the repository's
`./data` directory. The optional ComfyUI gateway is enabled with
`docker compose --profile comfy up --build`.

The control-plane service uses Docker Desktop host networking so outbound
HTTPS requests to configured cloud providers follow the same route as the
Windows host. It listens on the internal host-network port
`PERSONAWEAVE_INTERNAL_HTTP_PORT` (8110 by default). A small `ingress` service
publishes the stable `PERSONAWEAVE_HTTP_PORT` (8100 by default) back to
Windows. Host networking must be enabled in Docker Desktop.

When using that profile, also set
`PERSONAWEAVE_DOCKER_INFERENCE_BASE_URL=http://127.0.0.1:8010` and
`PERSONAWEAVE_DOCKER_INFERENCE_API_KEY` to the same value as
`PERSONAWEAVE_GATEWAY_SHARED_KEY`.

For an existing database created by the WSL development workflow, keep
`PERSONAWEAVE_LEGACY_DATA_ROOT=/mnt/e/yhr/workspace/PersonaWeave/data` in the
`.env` file. Compose mounts the same `./data` directory at that compatibility
path so old `file:///mnt/e/...` asset URIs remain readable. New assets are
written under `/app/data` and do not require WSL.

The control-plane container has a default 2 GB memory limit, no additional
container swap allowance, 2 CPU limit and 256 process limit. Adjust
`PERSONAWEAVE_MEMORY_LIMIT` and `PERSONAWEAVE_CPU_LIMIT` in `.env` for the
host's capacity. Workflow files are mounted read-only when the optional gateway
is enabled.

Formal final validation requires both reviewer model variables. The model IDs
must be distinct; partial configuration or the same ID in both variables is
rejected at startup. Both reviewer verdicts and the deterministic local
source/result comparison must pass before a result is eligible for human batch
approval.

Run one gateway worker per SQLite database. The completed-response cache is
durable across restarts, while the in-process lock prevents duplicate execution
inside that worker.

## Asset transport

Local `file://` assets are converted to data URIs after verifying that their
resolved path remains under `PERSONAWEAVE_ASSET_ROOT`. Public HTTPS and signed
object-store URLs pass through unchanged.

For production batches, use object storage with short-lived signed URLs rather
than large JSON data URIs. The `AssetResolver` is the single replacement point.

## Secrets

Use a deployment secret manager or an ignored local `.env`. Never place keys in
workflow JSON, database rows, screenshots, issue descriptions, or logs.
