# Yunwu real-photo preview attempt — 2026-08-13

Scope: the user explicitly authorized uploading `data/images/DSC_6193.JPG` and
one potentially billable prompt-based edit intended to replace both detected
faces with one consistent fictional identity.

## Outcome

The request did not produce an image. The local ComfyUI job failed while
writing the multipart request body to Yunwu with `httpx.WriteTimeout` after
about 307 seconds. No provider generation response was received by
PersonaWeave. Whether the provider charged this interrupted request cannot be
established from the local response, so the system did not retry it
automatically.

There is no result file under `.runtime/preview-results/` for this attempt.

## Corrective change and local verification

The image-edit node now fits high-resolution inputs and masks to a documented
provider canvas before upload, then maps the returned candidate back to the
original resolution before protected local compositing. For this image, the
provider upload is now 1536 by 1024 and roughly 2.36 MB as PNG instead of a
full-resolution 5568-by-3712 PNG.

Verification after the change:

- Python compilation passed;
- all 27 project tests passed;
- ComfyUI restarted and loaded `PersonaWeaveYunwuMaskedImageEdit`;
- the non-billable local mask-guard workflow passed with zero changed pixels
  outside its authorization mask;
- no second Yunwu request was made.

A retry requires a new explicit approval because it may create another charge.
