# Yunwu Seedream 5.0 Pro contract

Verified against the rendered Yunwu Apifox page supplied by the user on
2026-08-14 and a real request through the local ComfyUI node.

- Endpoint: `POST https://yunwu.ai/api/v3/images/generations`
- Model: `doubao-seedream-5-0-pro-260628`
- Content type: JSON
- `prompt` is required.
- `image` accepts one source URI or an array of source/reference URIs. Yunwu's
  gateway also documents `images` as a compatibility alias; PersonaWeave uses
  the official singular field.
- The first image is the immutable source canvas. Additional images are
  identity/style references described by the prompt.
- `response_format` supports `url` and `b64_json`; PersonaWeave requests `url`.
- `size` accepts `1K`, `2K`, and the documented pixel sizes including
  `2496x1664` and `1664x2496`.
- `n`, `output_format`, `seed`, `guidance_scale`, and prompt optimization are
  optional; PersonaWeave sends only fields enabled by its explicit profile.

The project keeps the OpenAI-compatible `/v1` base URL for multimodal calls.
Image routing is controlled by `PERSONAWEAVE_YUNWU_IMAGE_API_MODE` and
`PERSONAWEAVE_YUNWU_IMAGE_API_PATH`; the adapter does not inspect model names.
For the documented Seedream profile, the v3 path is rebuilt from the URL origin
so it never produces the invalid `/v1/api/v3/...` path. Multipart edit profiles
retain the `/v1` base path.
