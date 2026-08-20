# Remote inference provider contract

PersonaWeave deliberately does not bind its domain model to ComfyUI node names.
A hosted ComfyUI deployment, commercial API, or custom GPU service can implement
the following HTTP contract.

## Optional legacy/local `POST /v1/analyze-faces`

This endpoint is not the default batch-analysis path. It remains available for
local diagnostics, face-region preview, and strict-mask assistance. Input
contains one source asset URI. Output must include stable face IDs,
pixel-space bounding boxes, detection confidence, and an optional batch-level
person cluster key. An uncertain cluster must set `needs_confirmation=true`.

Default person discovery and proposed cross-image grouping are performed by the
configured multimodal model over the complete source set. That result must still
obey the same fail-closed rule: the provider must not guess a person's identity from appearance or descriptive
attributes. Face embeddings and clustering are evidence for a proposed grouping;
the user confirms the grouping.

The batch result's coarse boxes are not authoritative geometry. Each occurrence
is expanded into an application-generated zoom tile with an independent 0-1000
grid. Tiles are supplied as independent images rather than concatenated into a
wide panel. Low-confidence or degenerate coordinate results do not replace the
original coarse anchor, and repair localization starts again from that anchor
with broader context. Tile-relative boxes are mapped back to the source and converted to a
deterministic, unpadded face-crop board for multimodal review. Hair, hats,
natural occlusion, and limited collar/shoulder context are acceptable when the
intended face is complete, dominant, and usable. Passed occurrences are frozen;
only failed occurrences enter one coordinate relocalization. A remaining,
explicit single-edge clipping issue may receive one deterministic directional
expansion followed by a final review. Every attempt's box and verdict remains in
the analysis job output. Severe identity-blocking occlusion or a failed final
review keeps the source in manual review and prevents automatic analysis
confirmation. Grid drawing and cropping are local pixel operations, not local
face detection.

During analysis confirmation, a targeted Pxx re-localization may rerun this
geometry workflow for only that person's existing occurrences across source
images. It updates boxes and audit history but must not change grouping or
create/remove occurrences. Concurrent targeted re-localizations for the same
batch are rejected to prevent stale coordinate writes. An unresolved occurrence
must expose its latest reviewed crop and accept exactly one explicit user
resolution: `manual_bbox`, `keep_original`, or `accept_occlusion` when the
provider has classified a source-inherent major occlusion, source-boundary cut,
or unusable source face. `manual_bbox` is bounds-checked and rejected when it
overlaps another occurrence. `keep_original` remains visible contract evidence
but is excluded from the editable face IDs for that source.

## `POST /v1/swap-face`

Input identifies exactly one source image plus one or more confirmed face
replacement operations. Formal requests include the complete compiled task
contract and its SHA-256. Compiled source evidence binds every face instance ID
to its confirmed person ID/code, pixel-space bounding box, and source
appearance; every editable face ID must resolve uniquely to that evidence. Each
operation repeats the contract hash and carries its confirmed
editable/protected attribute partition, bounded target identity profile,
batch-level reference assessment, source-occurrence reference coverage, and
reference SHA-256s in the same order as its runtime reference URIs. Runtime
storage URIs are intentionally outside the immutable contract hash.
The request's immutable `review_reasons` set is the sole authority for
source-level review policy. Computed `requires_manual_review` projections are
not serialized into the immutable request or canonical contract.
The provider must reject missing or mismatched contract evidence rather than
infer permissions, identity evidence, or review policy.

Each operation contains a face instance, its bounding box, and an optional
target identity with reference assets. The immutable numbered provider
instruction is derived from the compiled contract. Runtime planner or reviewer
guidance is explicitly subordinate and must not replace, omit, or expand that
instruction. Before generation, the provider adapter verifies the loaded source
bytes against the compiled source SHA-256 and every loaded reference against
the corresponding operation SHA-256.

Formal source images are interpreted exactly as stored. Raster compilation and
provider execution reject sources with a non-identity EXIF orientation, any
alpha channel, or palette/info transparency; callers must supply an opaque
source whose pixel dimensions do not depend on metadata normalization.

A formal request additionally carries the canonical authorization-mask URI,
SHA-256, width, and height for its source. The canonical artifact is a
source-resolution binary `L`-mode PNG compiled from locally validated semantic
include contours and contained exclusion holes, then intersected with the
confirmed editable face-box union. Its digest is immutable. A provider adapter
may deterministically resize and invert those pixels to satisfy its multipart
API, but that derived payload is not byte-equal to the canonical artifact and
must not replace its evidence.

All replacements for the same image are executed as one atomic job so that
parallel results cannot overwrite one another. The provider must return a result
asset URI and preserve the request's complete `review_reasons` set with the
result. `reference_insufficiency` and `mask_uncertain` make the result
review-only; neither reason may be dropped by generation or validation.

Formal batch jobs set `preserve_outside_mask=true`; the authoritative local
post-composite decodes the canonical source-resolution raster and restores
source pixels everywhere it is black. Direct model-native and controlled-mask
preview requests remain separate advisory paths: the controlled preview's
normalized face oval/ellipse is deliberately not the formal authorization mask.
Whole-image regeneration without this post-composite is not compliant for
formal batch delivery.

## `POST /v1/validate-result`

Input contains source and result asset URIs plus the exact replaced and protected
face instance IDs. Validation combines:

1. expected-target face similarity;
2. protected-person face similarity;
3. pixel difference outside all authorized masks;
4. detection of missing or duplicated faces;
5. cross-result consistency for peers mapped to the same target identity.

PersonaWeave sends an independent copy of the complete validation request to
each of the two configured formal Yunwu reviewers concurrently. Both reviewer
models are required and their model IDs must be distinct; a missing reviewer
prevents validation rather than falling back to the inference gateway. Their
checks, reasons, provider identities, and pass/fail verdicts are retained
separately as `reviewer_a` and `reviewer_b`. A result is eligible for human
batch approval only when both verdicts and the local source/result comparison
pass. Multimodal verdicts are quality inspections, not biometric identity
proof. Reviewer failures remain scoped to that image job and do not cancel peer
image jobs.

## Error responses

Generation endpoints return provider failures as a FastAPI object `detail`.
Clients must use the stable `code` and `retryable` fields rather than parsing
the localized `message`:

- `provider_policy_rejected`: HTTP 422, not retryable;
- `provider_rate_limited`: HTTP 429, retryable;
- `provider_timeout`: HTTP 504, retryable;
- `provider_request_rejected`: HTTP 502, not retryable;
- `provider_unavailable`: HTTP 502, retryable.
- `workflow_execution_failed`: HTTP 502, not retryable; used when ComfyUI fails
  without validated provider error facts.

The object may also contain bounded diagnostic facts: `upstream_status`,
`provider_code`, `provider_type`, `request_id`, and `retry_after_seconds`.
These fields never contain raw provider response prose, prompts, signed URLs,
or credentials. Malformed upstream errors are normalized to a safe canonical
fallback rather than forwarded verbatim.

## Transport requirements

- Requests and callbacks carry a stable idempotency key.
- Asset URLs should be short-lived signed URLs in production.
- Provider credentials stay in a secret manager, never workflow JSON.
- Logs redact authorization headers, signed query parameters, and raw face
  embeddings.
- Every result records provider, model version, workflow version, and input hash.
