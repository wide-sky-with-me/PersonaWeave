# ComfyUI workflow bindings

This directory contains validated ComfyUI API-format workflow specifications:

- `analyze.json` — active local MediaPipe detection workflow
- `swap.json`
- `validate.json`

Each file wraps the native API-format prompt with metadata used by the gateway:

```json
{
  "name": "workflow-name-and-version",
  "version": 1,
  "model": "optional-model-name",
  "prompt": {},
  "output": {
    "node_id": "output-node-id",
    "field": "text-or-images",
    "kind": "json-or-image"
  }
}
```

The prompt may use exact typed placeholders. The gateway replaces them before
submitting the prompt:

| Placeholder | Available in | Value |
| --- | --- | --- |
| `{{request}}` | all | Full request object with uploaded ComfyUI filenames |
| `{{request_json}}` | all | Same request serialized as JSON |
| `{{source_image}}` | all | Uploaded source filename |
| `{{authorization_mask}}` | swap | Uploaded canonical source-resolution authorization-mask PNG |
| `{{provider_instruction}}` | swap | Immutable contract instruction plus explicitly subordinate adaptive guidance |
| `{{reference_images}}` | swap | Flat list of uploaded reference filenames |
| `{{reference_images_json}}` | swap | Reference list serialized as JSON |
| `{{result_image}}` | validate | Uploaded result filename |

`analyze.json` must return a JSON object containing `faces`. `swap.json` must
return one image from an output node. `validate.json` must return `passed`,
`checks`, and `reasons` as JSON.

A configured formal `swap.json` must route `{{source_image}}`, the red
channel of `{{authorization_mask}}` (via `LoadImageMask`), and
`{{provider_instruction}}` into `PersonaWeaveYunwuMaskedImageEdit` in
`multipart_edits` mode. The gateway rejects a graph that omits this composed
instruction binding, is unmasked, or uses JSON generation during readiness
checks. It
verifies the canonical mask's SHA-256, source dimensions, binary `L` pixels,
and PNG encoding before upload, then restores source pixels outside that same
canonical raster after generation. The multipart provider mask is a
deterministic resized/inverted derivative, not the canonical evidence artifact.

The missing `swap.json` and `validate.json` files are intentional: the gateway
must remain not-ready until qualified identity replacement and validation
models are selected. The files under `examples/` demonstrate the binding shape but reference
illustrative `PersonaWeave*` custom nodes. They are intentionally not copied to
the active directory: an uninstalled node must never make the health endpoint
claim that a production workflow is ready.

Export the real workflow using ComfyUI's **Save (API Format)** option, add the
wrapper fields above, replace the appropriate fixed widget inputs with
placeholders, and then validate it against the exact hosted instance's
`/object_info` endpoint.
