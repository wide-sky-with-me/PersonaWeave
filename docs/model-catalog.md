# AI model catalog

This document records the current public model routing contract. The executable source of truth is [`config/ai_gateway.yaml`](../config/ai_gateway.yaml); `core/ai/configuration.py` validates that file for `AIGateway` and `GET /api/v1/ai/models` exposes its non-secret image-model metadata to the frontend. A configured alias must not be documented as available unless it exists in that YAML file.

## Native deployment configuration

`model_list` is the single native LiteLLM Router list for both vision and image-edit deployments. `model_name` is a stable application alias, while `litellm_params.model` selects the actual provider/model ID and may resolve an `os.environ/NAME` reference. `image_models` contains only application metadata (`model_name`, `display_name`, `description`, `generation`) referencing the same list: no `provider`, `params` or second connection configuration. Multiple deployments may share an alias; image metadata has one entry per image alias.

Vision calls use `Router.acompletion`; image calls use `Router.aimage_edit`. In the installed LiteLLM 1.98.0, the image-edit method is dynamically registered on the Router instance, not necessarily declared as a class attribute. Provider protocols belong to LiteLLM: there is no handwritten supplier HTTP adapter or private-protocol fallback. See the official [Router documentation](https://docs.litellm.ai/docs/routing) and [image edits documentation](https://docs.litellm.ai/docs/image_edits). Native support is endpoint-specific: an arbitrary base URL or working chat service does not establish image-edit support. Check the selected provider/model and installed-version compatibility before use.

LiteLLM 1.98.0 drops the call-level image-edit `client`, so the gateway registers its transport-only wrapper through native `router.factory_function(..., call_type="aimage_edit")`: each attempt creates a redirect-disabled `AsyncHTTPHandler` with response hooks, calls `litellm.aimage_edit`, then closes the handler. Native Router deployment selection, retries and fallbacks remain intact; no global client or model object is modified. Remove this compatibility registration once upstream preserves the client and the transport-safety regressions pass.

## VLM aliases

| Alias | Backend model | Current use |
| --- | --- | --- |
| `personaweave-vision-default` | `os.environ/AI_VISION_MODEL` | Default vision, identity arbitration, refinement diagnosis and advisory review |

## VLM role routing

Business code selects a semantic role instead of embedding a model alias:

| Role | Configured alias |
| --- | --- |
| `default` | `personaweave-vision-default` |
| `identity_arbitration` | `personaweave-vision-default` |
| `refinement` | `personaweave-vision-default` |

`AIGateway` validates that these three roles reference vision deployments in `model_list`. Users may configure distinct vision aliases per role; routing does not change the ArcFace-first local identity decision.

## Image-edit aliases

| Alias | Display name | Backend model | Provider |
| --- | --- | --- | --- |
| `personaweave-refine-default` | 默认图像编辑模型 | `os.environ/AI_IMAGE_MODEL` | `litellm` |

Default image alias: `personaweave-refine-default`.

The checked-in catalog contains only these two generic aliases, not a supplier model inventory. `GET /api/v1/ai/models` preserves the public response shape: `provider` is `litellm`, `model` is the configured actual model ID (multiple deployments may be comma-separated), and no connection URL or credential is exposed. The frontend places `default_alias` first and otherwise preserves configuration order; no frontend service-management UI or separate server is required.

## Environment configuration

Each deployment resolves its model, URL and credential independently:

| Capability | Model | Base URL | API key |
| --- | --- | --- | --- |
| Vision | `AI_VISION_MODEL` | `AI_VISION_API_BASE` | `AI_VISION_API_KEY` |
| Image edit | `AI_IMAGE_MODEL` | `AI_IMAGE_API_BASE` | `AI_IMAGE_API_KEY` |

The checked-in YAML uses `os.environ/` references for these six values. `.env.example` uses `openai/gpt-4o` and `openai/gpt-image-1` only as replaceable model examples, not a promise of compatibility, account access, price or availability. Users set their own URLs and keys locally; never commit credentials. Vision and editing can use different services. Additional deployments can reference additional user-defined variables without changing business aliases.

For OpenAI-compatible endpoints, use explicit `openai/<actual-model-id>` values for both model settings. LiteLLM removes the protocol prefix before sending the model ID; relying on a bare new model name can fail provider detection when the local model registry does not recognize it. Router initialization failures are reported as local AI routing configuration errors, separately from upstream HTTP errors, without exposing raw exception bodies. See `core/ai/gateway.py`, `core/ai/types.py` and `tests/test_litellm_gateway.py`. No automatic prefix guessing or additional model requests are introduced.

Startup validates configuration and non-empty environment references without paid calls. Missing or invalid configuration sets `ai_refinement_ready=false`: the model catalog and new refinement turn commands return 503, while local swapping and persisted session viewing/acceptance remain available. Configuration validation failure disables optional AI rather than stopping the API. The separate `/api/v1/vlm/arbitrate` endpoint retains its advisory response contract: gateway/call failures may return HTTP 200 with `is_success=false`, while construction errors return 400; these are not successful identity matches. Readiness is not upstream authentication or endpoint availability. Unknown price is not free: the default catalog must not fabricate zero-cost pricing.

## Generation policy

Each image alias declares exactly one policy in `image_models[].generation`; all deployments behind that alias must support it. Do not infer limits from model names or silently translate unsupported parameters.

1. **Provider-native default:** `{size: "auto"}`. The default delegates size to the provider; it does not promise 4K, a fixed resolution or exact source-canvas dimensions.
2. **Discrete supported sizes:** `{sizes: ["1024x1024", "1536x1024", "1024x1536"]}`. Choose the entry with the closest canvas aspect ratio. These numbers illustrate the syntax only; configure sizes supported by the selected image-edit endpoint.
3. **Custom dynamic range:** `{max_side, min_side, grid, max_pixels?, delimiter: "x"}`. All required bounds are positive valid declarations, `min_side <= max_side`, `grid` controls alignment, and optional `max_pixels` caps total pixels. Declare this only when the service supports such dimensions; missing, conflicting or invalid policies fail configuration validation.

[`generation_parameters()`](../core/refinement/image_ops.py) interprets the configured policy without provider-name guesses. Candidate normalization follows the whole-image output contract; it does not turn a requested size into evidence of native detail, preserve non-selected pixels by compositing, or bypass human review.

## Routing, retries and fallbacks

Default gateway request timeout: `300` seconds (5 minutes), from `router_settings.timeout`. The image deployment overrides this with `600` seconds. Refinement analysis/review have a separate 330-second stage deadline to leave room for evidence encoding and validation around the default vision request. Provider-side deadlines may be shorter; increasing the application timeout does not guarantee a successful response.

The default `router_settings` declares `num_retries: 0` and `fallbacks: []`; image deployment `litellm_params.max_retries` is `0`. For custom configurations, the loader also supplies `num_retries=0` when omitted and `max_retries=0` for every deployment when omitted, preserving explicit user values. These defaults avoid automatic repeated paid generation, not a guarantee about supplier billing. Users may explicitly configure retries and same-capability fallback aliases; additional attempts can incur additional charges. Multiple deployments under one alias must share capability. Vision cannot fall back to image editing or vice versa; image fallback aliases must have identical `generation` metadata so size semantics do not change silently.

Structured-vision response repair is separate from Router retries. `AIGateway.structured_vision_completion(..., max_text_repairs=2)` defaults to at most two additional pure-text JSON repair calls for callers such as identity arbitration; these contain no images but can still be billed. **Conversational refinement analysis/review explicitly pass `max_text_repairs=0`**, include the complete JSON schema in the initial system prompt, and surface invalid responses without automatic repair calls. The budget is checked locally and never forwarded to the provider. User-configured Router retries/fallbacks remain independent and may create additional charges even on a zero-repair refinement call.

## Migration

1. Preserve existing local `.env` and custom YAML; do not overwrite credentials, runtime data, task history or staged candidates. Use `.env.example` only as a template and privately edit the six generic `AI_VISION_*` / `AI_IMAGE_*` values above. Old `DMXAPI_API_BASE` / `DMXAPI_API_KEY` variables are incompatible and are not automatically translated.
2. Replace the old supplier-specific catalog with native `model_list` deployments for both capabilities. Move actual model/connection settings to `litellm_params`; reduce each `image_models` item to its alias and display/generation metadata. Old image `provider`/`params` configurations are rejected, not maintained as a second schema.
3. Explicitly choose models supported by the relevant LiteLLM endpoint; set `vision_roles`, `default_image_alias`, generation policies and optional same-capability fallbacks. Retired aliases are not silently mapped to the new defaults. Keep retries at zero unless you accept the possible repeated cost.
4. Restart the backend to load configuration. Check local readiness and the non-secret model catalog first; do not treat readiness or offline tests as a paid provider smoke test. Any real generation must be a separate explicit user action.
5. Retired single-task generation/retry routes return 410 rather than selecting another model. Existing legacy candidate acceptance still uses `expected_revision` and `candidate_digest`: publish only the exact verified staging bytes, without rerouting or regenerating. For new editing, explicitly create a session with a supported alias and verified evidence; the new session also validates its selected version. Whole-image-plus-text generation and human-only acceptance remain unchanged.

## Maintenance rule

When adding, removing, renaming, or changing a model:

1. update native `model_list`, semantic `vision_roles`, image metadata/generation and environment references in `config/ai_gateway.yaml`;
2. update the alias and role tables here in the same change, without inventing a supplier-specific default inventory;
3. verify endpoint and generation compatibility using upstream primary documentation and offline integration tests; label any real-call evidence separately and never imply offline tests prove live availability;
4. run `tests/test_documentation_consistency.py` and the gateway/API tests.

See [documentation-governance.md](./documentation-governance.md) for the repository-wide authority hierarchy.
