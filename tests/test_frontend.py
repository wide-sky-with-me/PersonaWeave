import re
from pathlib import Path


def test_react_workbench_bundle_is_present_and_self_consistent() -> None:
    static_root = Path(__file__).parents[1] / "src" / "personaweave" / "static"
    index = (static_root / "index.html").read_text(encoding="utf-8")

    assert "PersonaWeave 工作台" in index
    assert '<div id="root"></div>' in index
    asset_paths = re.findall(r'(?:src|href)="/app/([^\"]+)"', index)
    assert asset_paths
    assert all((static_root / asset_path).is_file() for asset_path in asset_paths)
