from pathlib import Path

import pytest

from personaweave.assets import AssetResolver
from personaweave.formal_masks import CompiledMaskArtifact, FormalCompilationResult
from personaweave.storage import Repository
from test_workflow import prepared_edit_compilation


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    instance = Repository(
        tmp_path / "personaweave.sqlite3",
        formal_mask_root=tmp_path,
    )
    instance.initialize()
    return instance


def test_store_rejects_mask_artifact_that_differs_from_contract(
    repository: Repository,
) -> None:
    batch, _, compilation = prepared_edit_compilation(repository, (True,))
    artifact = compilation.masks[0]
    mismatched = CompiledMaskArtifact(
        evidence=artifact.evidence.model_copy(
            update={"source_asset_code": "I99"}
        ),
        png_bytes=artifact.png_bytes,
    )

    with pytest.raises(ValueError, match="does not match contract evidence"):
        repository.store_compiled_contract(
            batch["id"],
            FormalCompilationResult(
                contract=compilation.contract,
                masks=(mismatched,),
            ),
        )

    assert repository.list_formal_masks(batch["id"]) == []
    assert not list(repository.formal_mask_root.glob("mask-*.png"))


def test_confirm_rejects_extra_uncertainty_acknowledgement(
    repository: Repository,
) -> None:
    batch, _, compilation = prepared_edit_compilation(repository, (True,))
    repository.store_compiled_contract(batch["id"], compilation)

    with pytest.raises(ValueError, match="exactly match"):
        repository.confirm_compiled_contract(
            batch["id"],
            compilation.contract.sha256(),
            acknowledged_mask_sha256s=["f" * 64],
        )


def test_confirm_detects_committed_mask_tampering(
    repository: Repository,
) -> None:
    batch, _, compilation = prepared_edit_compilation(repository, (True,))
    repository.store_compiled_contract(batch["id"], compilation)
    mask = repository.list_formal_masks(batch["id"])[0]
    mask_path = AssetResolver(repository.formal_mask_root).local_path(mask["storage_uri"])
    assert isinstance(mask_path, Path)
    mask_path.write_bytes(b"not a canonical mask")

    with pytest.raises(ValueError, match="digest is invalid"):
        repository.confirm_compiled_contract(
            batch["id"],
            compilation.contract.sha256(),
        )
