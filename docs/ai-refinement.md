# AI Refinement Architecture

This document is the canonical design contract for PersonaWeave AI refinement. It supersedes earlier descriptions of localized generation, identity-reference conditioning, landmark realignment, masking, and local compositing in the refinement path.

## Purpose

AI refinement repairs visible defects in one or more selected people while keeping the human in control of diagnosis, instructions, retries, and final acceptance. The image-edit provider operates on the current flawed whole image and returns a whole-image result. PersonaWeave normalizes and stages that result without reconstructing it from local regions.

## Architecture

The refinement pipeline has five conceptual stages:

1. **Person selection and visual location.** The human selects the people to refine from the faces already associated with the current image. The diagnostic VLM derives a concise textual visible locator for each person (for example, relative position, clothing, pose, and other non-identity visual cues).
2. **One diagnostic VLM call.** A single VLM request compares the complete pre-swap target (when available) with the complete current swapped image, organizes a structured defect list per selected person, and returns each person's textual visible locator. The call does not generate pixels.
3. **Human supplementation.** The human reviews the diagnosis and may add, remove, or clarify instructions. Human instructions remain distinct from model-observed defects so provenance is visible.
4. **Whole-image generation.** The image model receives exactly one image: the current flawed whole image. Its textual instructions contain only the selected people's concise visible locators, defect lists, and human supplements. No identity reference image, face crop, mask, landmark image, or other image attachment is sent.
5. **Advisory whole-image review.** One VLM call reviews the provider's normalized whole-image result in whole-image context. The normalized candidate is staged before review; a review outage is recorded as advisory `review_error` and does not discard or fail the generated candidate. The review is evidence only: it never accepts, rejects, composites, or publishes automatically. The human decides whether to accept, retry, cancel, or continue refining.

All VLM and image-model access continues through `AIGateway` and configured model aliases.

## Contracts

### Preflight input

A diagnostic request identifies the current flawed whole-image revision, an optional complete pre-swap target for comparison, selected request-scoped person IDs, their current-image face positions for correlation, and optional human notes. The server securely resolves the images, verifies that they are decodable and within configured limits, and sends only complete images to the VLM. A missing or stale current image fails without generation; the generative model itself receives only the current flawed whole image plus the resulting text locators, never the comparison image or geometric annotations.

### Diagnostic VLM output

The single diagnostic call returns a contract-validated collection with one entry per selected person:

- stable request-scoped person ID;
- concise textual visible locator grounded in the current image;
- zero or more specific, observable defects;
- optional concise suggested instruction.

Defects must remain concrete and visually verifiable. The locator is descriptive, not biometric: it exists only to tell the image model which visible person in this image the text refers to.

### Generation request

The image-model request contains:

- **one image attachment:** exactly the current flawed whole image for the active revision;
- a concise whole-image edit instruction;
- for each selected person, the textual visible locator, complete current defect list, and human supplement;
- constraints to avoid unrelated changes, expressed as instructions rather than masks or compositing guarantees;
- the selected image-model alias and provider-compatible generation parameters.

It must not contain identity reference images, gallery images, per-person crops, masks, landmarks, annotated images, or images from an earlier revision. Retries use the active current candidate as the new flawed whole image only when the user explicitly chooses to continue from it.

### Provider result

The provider returns a whole-image result. PersonaWeave validates and normalizes its encoding, orientation, color mode, dimensions, and metadata according to the output contract, computes its digest, and retains that normalized whole image directly as the staged candidate.

There is no `apply_candidate` step in this architecture. Refinement does not landmark-align generated faces, apply masks, locally composite pixels, or rebuild the result from the input image.

### Advisory review output

The single post-generation VLM call compares the complete pre-swap target when available, complete active baseline, and entire normalized candidate. It returns contract-validated advisory findings: requested-defect improvement, newly visible defects or regressions, apparent unrelated whole-image changes, concise region-grounded evidence, and optional scores or recommendation clearly labeled advisory.

The review cannot mutate task state to accepted, block acceptance as a mandatory quality gate, or publish an image. The UI presents it to the human decision-maker. If review fails, the candidate remains available with a `review_error` warning and is still eligible for explicit human acceptance.

## State Flow

The implemented success flow is:

`QUEUED → REFINING → REVIEWING → REVIEW_REQUIRED → ACCEPTING → ACCEPTED`

- Before task creation, the frontend performs person selection and invokes the one diagnostic VLM call; the human can edit its locators and instructions.
- `REFINING` securely loads the active whole image and invokes the selected image model with that one image and concise text.
- `REVIEWING` normalizes and stages the whole-image provider result, then performs one advisory whole-image VLM review.
- `REVIEW_REQUIRED` waits for a human decision.
- `ACCEPTING` atomically publishes the exact staged candidate whose revision and digest were confirmed.

Cancellation paths remain `CANCEL_REQUESTED → CANCELLED`; unrecoverable errors enter `FAILED`. Retry and recovery preserve enough task metadata to resume safely without confusing revisions or publishing partial work. Retrying generation or review does not silently change the active input revision. `LOCALIZING` remains an accepted legacy persisted status during migration, but new tasks do not emit it. Pre-task diagnosis is deliberately not represented by `PREFLIGHTING` or `DIAGNOSIS_REQUIRED` task states because no generation task exists yet.

## Invariants

1. **Current-image-only generation:** the image model receives exactly the active flawed whole image and no other image.
2. **Text-only person targeting:** selected people are identified to the image model by concise visible locators and defect/instruction text, never identity references.
3. **One diagnostic VLM call:** pre-generation diagnosis is one whole-image call organizing all selected people.
4. **Human-editable intent:** humans can supplement or correct instructions before generation; model diagnosis is not silently treated as immutable intent.
5. **Direct whole-image retention:** the normalized provider whole-image result is the staged candidate. No `apply_candidate`, landmark alignment, mask application, or local compositing is allowed in the refinement path.
6. **One advisory review call:** post-generation quality review is one whole-image VLM call and is never an automatic acceptance gate.
7. **Human acceptance:** only an explicit human action can accept a candidate.
8. **Two-stage storage:** unaccepted candidates remain under `outputs/refinement_staging/{task_id}/`; accepted outputs go to `outputs/api_results/refinement/accepted/` only during acceptance.
9. **Optimistic concurrency:** acceptance validates both `expected_revision` and `candidate_digest`; mismatch returns a conflict rather than overwriting newer work.
10. **Exact-candidate publish:** acceptance publishes the normalized staged bytes associated with the confirmed digest, not a regenerated, recomposited, or re-encoded substitute.
11. **Atomic publish:** formal output and accepted task metadata become visible atomically; failure leaves no partially published accepted result.
12. **Secure image loading:** model inputs are loaded only through allowlisted, size-limited, decode-validated mechanisms with path/URL protections; model-supplied or user-supplied paths are never trusted directly.
13. **Cancellation and recovery:** long-running calls honor cancellation at safe boundaries; retry/recovery is idempotent and preserves revisions, digests, staging isolation, and non-sensitive failure diagnostics.
14. **No partial publish:** diagnosis, generation, normalization, review, cancellation, and failure never expose a candidate in the accepted directory.

## Model Routing

- All model calls use `AIGateway`; business code must not call provider SDKs directly.
- VLM aliases use the `personaweave-vision-*` namespace. Refinement diagnosis and advisory review use the dedicated `personaweave-vision-refinement` alias so their stronger visual-change detector and fallback chain do not alter identity-arbitration behavior; each stage still performs exactly one whole-image call.
- Image-edit aliases use the `personaweave-refine-*` namespace. Provider capabilities and whole-image resolution limits remain configuration-driven and must be verified before adding or changing aliases.
- Task creation may select one configured image alias; the alias is persisted with the request and reused by default for later generations of that task. The UI obtains the complete non-secret image-model catalog from `GET /api/v1/ai/models`; exact aliases, backend model IDs, providers, fallback order, and the default are maintained in [model-catalog.md](./model-catalog.md) and `config/ai_gateway.yaml`, not duplicated here.
- Retry may select another configured image alias. The chosen alias is recorded with the revision and candidate metadata.
- Provider-specific adapters normalize responses to the common image result contract. They must not introduce extra reference images or hidden localized processing.
- Secrets and base URLs remain environment-resolved configuration; logs and task errors must not expose credentials or sensitive provider payloads.

## Non-goals

The AI refinement subsystem does not:

- perform face swapping or identity matching;
- use portrait-library, gallery, source-identity, or target-identity reference images during refinement generation;
- guarantee unchanged background or non-selected pixels through masks or byte-preserving compositing;
- crop, align, warp, mask, or locally composite the provider output;
- automatically accept or reject a candidate based on VLM scores or findings;
- publish intermediate, failed, cancelled, or merely reviewed candidates;
- replace the existing local face-swap pipeline or its separate masking and identity controls;
- infer a person's real-world identity from the textual visible locator.

## Migration Rule

Any older refinement documentation or tests that require `RefinementService.apply_candidate()`, localized candidate review, identity-reference conditioning, landmark realignment, anatomical masks, manual-protection compositing, or automatic VLM quality gates describe the retired design and must not be treated as current architecture. Preserve the operational safeguards listed above while migrating implementation and tests separately from this documentation-only change.

## Verifiable evidence

- Lifecycle enum and request/response contracts: [`api/schemas.py`](../api/schemas.py), especially `RefinementTaskStatus` and `RefinementTaskCreateRequest`.
- Emitted transitions, staging-before-review, advisory review failure, and digest-based acceptance: [`api/refinement_task_manager.py`](../api/refinement_task_manager.py).
- One-image generation and provider canvas normalization: [`core/refinement_service.py`](../core/refinement_service.py), especially `refine_whole_image()` and `normalize_to_canvas()`.
- Structured diagnostic and whole-image review contracts: [`core/vlm_contracts.py`](../core/vlm_contracts.py), especially `BatchFaceReview` and `WholeImageQualityReview`.
- Regression evidence: [`tests/test_refinement_service.py`](../tests/test_refinement_service.py), [`tests/test_refinement_tasks.py`](../tests/test_refinement_tasks.py), and [`tests/test_vlm_contracts.py`](../tests/test_vlm_contracts.py).

Documentation authority and synchronization rules are defined in [documentation-governance.md](./documentation-governance.md).
