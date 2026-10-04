# Conversational refinement prompt contracts

Prompt version: `conversation-refinement-v5`.

This document records the prompt provenance of the implemented boundary in `core/refinement/conversation.py`, `contracts.py`, `prompts.py` and `evidence.py`: the version, the official sources the prompts follow and the offline evidence. Behavior is defined in [refinement-analysis.md](refinement-analysis.md) (evidence, diagnosis, untrusted data), [refinement-generation.md](refinement-generation.md) (generation, review) and the authoritative [ai-refinement.md](ai-refinement.md); this file introduces no acceptance policy or second model registry.

## Official references

Only material actually retrieved is relied on; where a page could not be fetched, only the official search excerpts are used and no claim is made about its full text.

1. [Anthropic — Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) (excerpts only): clear task instructions, explicit context and output constraints, structurally separated variable data, diverse examples. Applied as separate system/data sections and examples for concrete defects, uncertainty and no-edit results.
2. [Anthropic — Vision](https://platform.claude.com/docs/en/build-with-claude/vision) (excerpts only): images before the query, and limits on small or low-quality images, localization and counting. Evidence images precede the final text query. Full photo evidence is JPEG-encoded with a maximum edge of 3200 pixels, keeping smaller inputs unchanged; bounded reference boards keep their own layout. This gives small faces more pixels but does not control provider-side sampling or promise a provider resolution limit. Tiny faces, side profiles and occlusions require uncertainty, not invented details. Stable person IDs are application bindings and visual similarity is not verified real-world identity; a refusal or unavailable comparison is never converted into an identity verdict.
3. [OpenAI Cookbook — Gpt-image-1.5 Prompting Guide](https://cookbook.openai.com/examples/multimodal/image-gen-1.5-prompting_guide), read from its [official notebook source](https://raw.githubusercontent.com/openai/openai-cookbook/main/examples/multimodal/image-gen-1.5-prompting_guide.ipynb): labeled goals/details/constraints, concrete visual descriptions, an explicit change-versus-preserve list, repeating invariants during iterative edits. We adopt these methods, not claims about identity accuracy, speed, providers, availability or resolution.

The tests validate offline contracts and request construction. The [two-round live test on a five-person photo](refinement-live-test-20260911.md) verified request composition and version chaining, but both candidates had visible quality regressions and the second review failed with HTTP 408 (its candidate was retained); it does not establish prompt effectiveness across photos or model configurations.

## Offline regression evidence

[tests/test_refinement_conversation.py](../tests/test_refinement_conversation.py) covers the prompt/evidence contracts listed in [refinement-generation.md](refinement-generation.md#evidence). Run: `uv --no-cache run --no-sync pytest tests/test_refinement_conversation.py`.
