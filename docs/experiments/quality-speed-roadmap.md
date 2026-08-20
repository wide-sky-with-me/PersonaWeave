# Quality and speed experiment roadmap

This worktree tests a staged replacement of subjective, repeated multimodal
checks with cheap deterministic gates and specialized face evidence. Formal
delivery remains fail-closed: an unavailable or ambiguous signal cannot be
converted into an automatic pass.

## Phase 1: deterministic formal gate

Implemented in this experiment:

- carry the canonical authorization-mask URI, SHA-256 and dimensions from the
  swap job into the validation job;
- authenticate and decode the local mask before remote review;
- require exact source-pixel equality outside the canonical mask;
- reject results with no measurable change inside an explicitly editable
  region;
- stop before both paid visual reviewers when this local gate fails.
- run optional provider-neutral objective validators between the deterministic
  gate and paid reviewers; any failed or unavailable objective signal stops the
  cascade.

The existing expanded-box and protected-person comparisons remain as
diagnostics and as the fallback for advisory requests that do not have a formal
mask.

## Phase 2: specialized face evidence

The validation executor now accepts provider-neutral objective validators.
Implement and configure the following evidence providers rather than binding
the domain model to one runtime:

1. face detector and five-point/mesh landmark provider;
2. licensed identity embedding provider with model/version metadata;
3. face parser and occlusion-mask provider;
4. expression, gaze and head-pose estimator.

Persist evidence by source SHA-256 and model version. Re-analysis should reuse
valid detection, landmark, embedding and mask records. Low-confidence matches
remain separate and require confirmation.

## Phase 3: local-first validation cascade

Run gates in increasing cost order:

1. source/result/mask integrity and dimensions;
2. exact pixels outside the authorization mask;
3. face count and expected occurrence coverage;
4. protected-person identity similarity;
5. target-reference identity similarity;
6. pose, expression, gaze and occlusion preservation;
7. cross-result target-identity consistency;
8. two multimodal reviewers only for surviving candidates.

Each metric must record the evaluator model, threshold and raw score. Thresholds
must be calibrated on consented project-domain images rather than copied from a
paper or treated as probabilities.

## Phase 4: generation speed

- crop and batch face regions instead of sending the full source to every
  specialized model;
- select 256, 512 or 1024 processing size from face area and blur;
- generate all replacements for one source atomically, then composite once;
- cache immutable reference profiles and source evidence;
- retry only the failed source/person pair;
- keep provider concurrency bounded and preserve idempotency keys.

## Acceptance metrics

Track metrics per face occurrence and per complete batch:

- wrong-person replacement and protected-person modification rate;
- missed replacement and duplicate-face rate;
- target identity false-accept and false-reject rate;
- authorization-mask-external changed pixel count;
- manual-review rate and automatic false-pass rate;
- p50/p95 analysis, generation and validation latency;
- paid model calls and cost per accepted image.

No phase removes abstention. Automation is acceptable only when uncertain
results are isolated instead of silently released.

## Research basis

The staged design follows recurring findings in face-swapping research rather
than treating one visual reviewer or one recognition embedding as sufficient:

- [SimSwap (2106.06340)](https://huggingface.co/papers/2106.06340) separates
  source identity injection from target expression and gaze preservation.
- [FaceDancer (2210.10473)](https://huggingface.co/papers/2210.10473) evaluates
  identity transfer together with pose, expression, lighting and occlusion.
- [BlendFace (2307.10854)](https://huggingface.co/papers/2307.10854) documents
  identity-attribute entanglement in commonly used recognition encoders,
  including hairstyle bias. An ArcFace-family similarity score therefore
  cannot be the sole automatic acceptance signal.
- [MFIM (2308.01536)](https://huggingface.co/papers/2308.01536) explicitly
  separates identity attributes from pose and expression using 3D facial
  evidence.
- [DynamicFace (2501.08553)](https://huggingface.co/papers/2501.08553) and
  [ID-constrained diffusion face swapping
  (2503.22179)](https://huggingface.co/papers/2503.22179) use decoupled facial
  conditions because identity and target-attribute conditioning can conflict.

Open-source availability does not imply production weight availability.
[InsightFace](https://github.com/deepinsight/insightface) publishes MIT code,
but its downloaded pretrained models are restricted to non-commercial research
and the INSwapper series requires separate licensing. SimSwap is CC BY-NC 4.0,
and FaceDancer is CC BY-NC-SA 4.0. This experiment therefore keeps objective
validators provider-neutral and does not add those weights as runtime
dependencies.
