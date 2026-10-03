"""Source boards describe the actual reference mixture, never a guessed photo."""

from types import SimpleNamespace

import numpy as np
import pytest

from scripts import diagnose_batch_strength as diagnostic


def test_source_board_labels_all_actual_references_and_true_best(monkeypatch):
    labels = []
    portraits = []

    def fake_tile(image, label, size=256):
        labels.append(label)
        portraits.append(int(image[0, 0, 0]))
        return np.zeros((size + 32, size, 3), np.uint8)

    monkeypatch.setattr(diagnostic, "tile", fake_tile)
    monkeypatch.setattr(diagnostic, "load_image_from_source", lambda value: np.full((8, 8, 3), int(value), np.uint8))
    refs = [{"path": str(value), "face": SimpleNamespace(bbox=np.array([0, 0, 8, 8]))} for value in (40, 80)]
    result = diagnostic.source_reference_board(
        refs,
        {
            "mode": "best-match",
            "selected_reference_index": 2,
            "gallery_weight": 0.8,
            "best_reference_weight": 0.2,
            "gallery_reference_weights": [0.25, 0.75],
        },
    )
    assert portraits == [40, 80]
    assert labels == ["Ref #1: g=0.250", "Ref #2: g=0.750 BEST"]
    assert result.shape == (294, 384, 3)
    labels.clear()
    diagnostic.source_reference_board(refs, {"gallery_reference_weights": None})
    assert labels == ["Ref #1: g=unknown", "Ref #2: g=unknown"]


def test_source_board_rejects_mismatched_weights():
    with pytest.raises(ValueError, match="reference count"):
        diagnostic.source_reference_board([{}], {"gallery_reference_weights": [0.2, 0.8]})


def test_failed_image_write_is_not_reported_as_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostic.cv2, "imwrite", lambda *_: False)
    with pytest.raises(OSError, match="Could not write"):
        diagnostic.write_image(tmp_path / "missing.jpg", np.zeros((8, 8, 3), np.uint8))
