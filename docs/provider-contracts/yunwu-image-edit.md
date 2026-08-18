# Yunwu image-edit contract

Checked against the public Apifox project at <https://yunwu.apifox.cn/> and one
explicitly approved billable synthetic request on 2026-08-13.

## Confirmed document shape

- Endpoint: `POST /v1/images/edits`
- Content type: `multipart/form-data`
- Required fields: `image`, `prompt`
- Optional fields include: `mask`, `model`, `n`, `quality`, `size`,
  `background`, and `moderation`
- The mask must be a PNG with the same dimensions as the source. Fully
  transparent pixels identify the editable area.
- The document explicitly names `gpt-image-1` for this endpoint and says its
  image result is base64 encoded.

## Qualified synthetic result

The `gpt-image-1` request completed successfully and returned a 1024-by-1024
PNG. PersonaWeave's post-composite pixel guard measured:

- 967,699 checked pixels outside the authorization mask;
- 0 changed pixels outside the authorization mask;
- 80,877 changed pixels inside the authorization mask.

Visual inspection confirmed the requested orange star appeared inside the
circle. The generated probe output remains under the ignored local runtime at
`.runtime/ComfyUI/output/PersonaWeave/probes/`.

## Unresolved before production use

The same published operation has internally inconsistent examples: its model
example says `jimeng-4.0`, while its declared success response is shaped like a
chat completion. The account model list also exposes newer models that the
operation text does not name. Therefore:

1. use only a model qualified against the masked-edit contract; the current
   configured model is `gpt-image-2`;
2. require user-confirmed identity mappings before sending real photos;
3. separately qualify multi-image identity references and face quality;
4. always restore original pixels outside the authorization mask and run the
   outside-mask pixel guard after generation.

## High-resolution upload boundary

PersonaWeave does not send the full-resolution PNG produced from an activity
photo. The provider candidate canvas is fitted proportionally to one of
`1536x1024`, `1024x1536`, or `1024x1024`; the authorization mask and optional
reference photo use the same transform. Any padding is protected by the mask.
After generation, the candidate is cropped back to the fitted region, mapped to
the original source size, and locally composited only inside the original face
mask. The final pixel guard still runs at the source resolution.

For `DSC_6193.JPG`, the local qualification reduced the provider source from
5568 by 3712 to 1536 by 1024 and encoded it as a roughly 2.36 MB PNG. This
qualification does not itself call Yunwu or incur a provider charge.

## GPT Image 2 qualification

On 2026-08-13, one explicitly authorized `gpt-image-2` request completed through
`POST /v1/images/edits`. PersonaWeave accepts both the older `data[0].b64_json`
shape and the provider's `data.b64_json` shape, plus HTTPS result URLs. The
two-face real-photo case returned a valid full-resolution PNG and the local
guard confirmed zero changed pixels outside the authorization mask.

This qualifies the transport and masked-composite contract, not final face-swap
quality. Both faces showed a consistent new identity, while hair, glasses, and
source-expression preservation remained below the product acceptance bar.

## Model-native strategy

The default `gpt-image-2` preview is a whole-image model-native edit without a
local detector, provider mask, or post-composite. GPT-5.6 receives the original
source, optional identity reference, and immutable user requirement. It writes
one complete initial prompt, then reviews each candidate against the original
with fixed thresholds. A failed review must write a complete replacement prompt
that preserves the original requirement and addresses visible defects. Every
round sends the original source to GPT Image 2; generated candidates are review
evidence only and never become the next editing canvas.

The path uses the qualified provider canvases (`1536x1024`, `1024x1536`, or
`1024x1024`); an attempted `3072x2048` call on 2026-08-13 disconnected without
a response after roughly four minutes. Multimodal review is advisory: it cannot
prove pixel preservation or biometric identity, so an exhausted loop remains
marked for human review.

## Production swap safeguards

Formal batch jobs use one compiled source-resolution binary grayscale
authorization mask per source containing an editable occurrence. KEEP-only
sources have no mask and create no swap job. Yunwu receives a deterministic
fitted/inverted multipart derivative of those canonical pixels; after
generation, the authoritative Python/Pillow compositor decodes the canonical
raster itself and retains candidate pixels only where it is white. The original
source supplies every other pixel.

Formal execution verifies the loaded source and every numbered target reference
against the SHA-256 evidence in the confirmed contract before any provider
upload. Formal sources with a non-identity EXIF orientation tag, an alpha
channel, or palette/image transparency are rejected during both mask compilation
and gateway loading; callers must supply an opaque, canonically oriented source.

This semantic contour raster deliberately replaces the old formal
bounding-box-ellipse path. It is also distinct from the normalized face-oval or
ellipse produced by optional controlled-mask preview nodes, which remains
preview-only. Model-native preview remains a full-frame advisory path and does
not claim pixel stability.

The Comfy gateway reuses owned HTTP connection pools, bounds reference
downloads/uploads globally, deduplicates repeated reference URIs per request,
and closes the pools at application shutdown. Numbered reference boards are
lossless PNGs.

Provider failures cross Comfy history as bounded structured facts and are
classified centrally as policy rejection, timeout, rate limit, request
rejection, unavailability, or local workflow failure. Public jobs expose a
stable safe message and retryability. In particular,
`OutputImageSensitiveContentDetected.PolicyViolation` becomes the
non-retryable `provider_policy_rejected` code rather than an opaque 502.
