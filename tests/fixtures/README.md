# API integration-test fixtures

This directory contains only the four images required by `tests/test_api.py`:

- `source-01.png` and `source-02.png`: source identity references.
- `group-01.png` and `group-02.png`: multi-person target images.

The rest of `data/` is local working data and remains ignored. Keep fixtures small, deterministic, free of generated outputs, and cleared for storage in the repository. Fixture images must not contain EXIF/device/location metadata or be copied from private user uploads; use only material whose repository storage and testing use have been explicitly cleared. If these images are replaced, rerun the full API test suite because its assertions require detectable faces and a successful swap.
