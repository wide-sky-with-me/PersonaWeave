"""The pair-runner patch must apply exactly once per anchor and fail loudly if upstream changes."""

import pytest

from scripts.diffusion_pair_patch import REPLACEMENTS


def upstream_like():
    return "".join(old for old, _new in REPLACEMENTS if not old.startswith("import argparse")).join(
        ["import argparse, os, sys, glob\n", ""]
    )


def apply(source):
    for old, new in REPLACEMENTS:
        if source.count(old) != 1:
            raise ValueError(old)
        source = source.replace(old, new)
    return source


def test_each_replacement_applies_exactly_once_and_installs_the_safe_loader_first():
    patched = apply(upstream_like())
    assert patched.index("safe_load.install()") < patched.index("enumerate(sorted(os.listdir(target_path)))")
    assert "for i in range(0):" in patched and "pair_img" in patched


def test_changed_upstream_is_rejected_instead_of_silently_half_patched():
    with pytest.raises(ValueError):
        apply(upstream_like().replace("for i in range(len(other_pred_x_0)):", "for j in range(3):"))
