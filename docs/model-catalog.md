# AI model catalog

This document records the current public model routing contract. The executable source of truth is [`config/ai_gateway.yaml`](../config/ai_gateway.yaml); `AIGateway` loads that file and `GET /api/v1/ai/models` exposes its non-secret image-model metadata to the frontend. A configured alias must not be documented as available unless it exists in that YAML file.

Snapshot verified: 2026-08-31.

## VLM aliases

| Alias | Backend model | Current use |
| --- | --- | --- |
| `personaweave-vision-review` | `qwen3-vl-8b-instruct` | General review and identity-arbitration baseline |
| `personaweave-vision-precision` | `qwen3-vl-30b-a3b-instruct` | Higher-precision fallback |
| `personaweave-vision-fallback` | `gemini-2.5-flash-lite` | Lightweight fallback for general review |
| `personaweave-vision-refinement` | `gpt-5.6-sol` | AI-refinement diagnosis and advisory whole-image review |
| `personaweave-vision-qwen-plus` | `qwen3-vl-plus` | Refinement fallback |
| `personaweave-vision-gemini-31-pro` | `gemini-3.1-pro-preview` | Refinement fallback |
| `personaweave-vision-gpt-56-terra` | `gpt-5.6-terra` | Refinement fallback |

The refinement VLM fallback chain is:

`personaweave-vision-refinement → personaweave-vision-gpt-56-terra → personaweave-vision-gemini-31-pro → personaweave-vision-qwen-plus → personaweave-vision-precision`

This chain applies to both pre-generation diagnosis and post-generation advisory review. It does not change the ArcFace-first identity decision used by local face swapping.

## VLM role routing

Business code selects a semantic role instead of embedding a model alias:

| Role | Configured alias |
| --- | --- |
| `default` | `personaweave-vision-review` |
| `identity_arbitration` | `personaweave-vision-precision` |
| `refinement` | `personaweave-vision-refinement` |

`AIGateway` validates that these three roles exist and reference entries in `model_list` before serving requests.

## Image-edit aliases

| Alias | Display name | Backend model | Provider |
| --- | --- | --- | --- |
| `personaweave-refine-gemini` | Gemini 3 Pro Image · 4K 质量 | `gemini-3-pro-image` | `dmx-gemini` |
| `personaweave-refine-gemini-flash` | Gemini 3.1 Flash Image · 快速 2K | `gemini-3.1-flash-image` | `dmx-gemini` |
| `personaweave-refine-seedream` | 豆包 Seedream 5.0 Pro · 合影默认 | `doubao-seedream-5-0-pro-260628` | `dmx-seedream` |
| `personaweave-refine-gpt` | GPT Image 2 · 质量优先 | `gpt-image-2-ssvip` | `dmx-images-edit` |
| `personaweave-refine-qwen` | Qwen Image 2.0 Pro | `qwen-image-2.0-pro` | `dmx-input-responses` |
| `personaweave-refine-qwen3` | Qwen Image 3.0 · 强编辑 | `qwen-image-3.0` | `dmx-responses` |
| `personaweave-refine-wan` | Wan 2.7 Image Pro · 创意编辑 | `wan2.7-image-pro` | `dmx-responses` |
| `personaweave-refine-plus` | Qwen Image Edit Plus | `qwen-image-edit-plus-20260226` | `dmx-responses` |
| `personaweave-refine-max` | Qwen Image Edit Max | `qwen-image-edit-max-2026-01-16` | `dmx-responses` |
| `personaweave-refine-fallback` | Qwen Image Edit · 兼容回退 | `qwen-image-edit` | `litellm` |

Default image alias: `personaweave-refine-seedream`.

The frontend places `default_alias` first and preserves configuration order for every other option; it does not maintain a second alias list. Each YAML image entry declares its own `generation` policy. [`RefinementService._generation_parameters()`](../core/refinement_service.py) interprets that policy: Gemini Pro requests 4K, Gemini 3.1 Flash requests 2K, GPT Image uses an aspect-preserving canvas up to 3072 on the long side, and response-style adapters use their declared grid, long-side and total-pixel ceilings. Every whole-image candidate still requires human comparison because an image model can redraw text, faces, clothing, composition, or global color.

## Maintenance rule

When adding, removing, renaming, or changing a model:

1. update `config/ai_gateway.yaml`, its semantic `vision_roles`, per-image-model `generation` policy and environment-variable-backed credentials;
2. update both tables in this document in the same change;
3. update provider-specific capability notes only after a real request confirms the response format and usable resolution;
4. run `tests/test_documentation_consistency.py` and the gateway/API tests.

See [documentation-governance.md](./documentation-governance.md) for the repository-wide authority hierarchy.
