"""Check the documented plist generator without touching launchd or user services."""

import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _generate(output: Path, deployment: str, address: str) -> subprocess.CompletedProcess:
    """Execute only the standard-library heredoc, never the platform commands."""
    document = (ROOT / "docs/macos-boot-guard.md").read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'\n(.*?)\nPY", document, re.S)
    assert len(blocks) == 1
    return subprocess.run(
        [sys.executable, "-", str(output)],
        input=blocks[0],
        cwd=ROOT,
        env={
            **os.environ,
            "PERSONAWEAVE_DEPLOYMENT_DIR": deployment,
            "PERSONAWEAVE_BIND_ADDRESS": address,
        },
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def test_launchagent_template_defines_the_documented_schedule():
    with (ROOT / "scripts/macos-colima-boot-guard.plist").open("rb") as stream:
        config = plistlib.load(stream)
    assert config["Label"] == "io.personaweave.boot-guard"
    assert config["StartInterval"] == 30
    assert config["RunAtLoad"] is True
    assert config["ProgramArguments"] == ["/bin/sh", "__DEPLOYMENT_DIR__/colima-boot-guard.sh"]
    assert config["EnvironmentVariables"]["PERSONAWEAVE_BIND_ADDRESS"] == "__BIND_ADDRESS__"


def test_documented_generator_substitutes_paths_without_xml_corruption(tmp_path):
    deployment = tmp_path / "deployment & photos"
    deployment.mkdir()
    output = tmp_path / "agents/guard.plist"
    result = _generate(output, str(deployment), "192.0.2.10")
    assert result.returncode == 0, result.stderr
    with output.open("rb") as stream:
        config = plistlib.load(stream)
    assert config["ProgramArguments"] == ["/bin/sh", str(deployment / "colima-boot-guard.sh")]
    assert config["EnvironmentVariables"] == {
        "PERSONAWEAVE_BIND_ADDRESS": "192.0.2.10",
        "PERSONAWEAVE_DEPLOYMENT_DIR": str(deployment),
    }
    assert config["StandardOutPath"] == config["StandardErrorPath"] == str(deployment / "boot-guard.log")
    assert "__DEPLOYMENT_DIR__" not in output.read_text()
    assert "__BIND_ADDRESS__" not in output.read_text()


def test_generator_preserves_an_existing_user_installation(tmp_path):
    output = tmp_path / "guard.plist"
    original = b"existing installation"
    output.write_bytes(original)
    assert _generate(output, str(tmp_path), "192.0.2.10").returncode != 0
    assert output.read_bytes() == original


@pytest.mark.parametrize("address", ["not-an-address", "::1"])
def test_generator_rejects_addresses_not_supported_by_the_guard(tmp_path, address):
    output = tmp_path / "guard.plist"
    assert _generate(output, str(tmp_path), address).returncode != 0
    assert not output.exists()


def test_generator_requires_an_existing_absolute_directory(tmp_path):
    output = tmp_path / "guard.plist"
    for deployment in [".", str(tmp_path / "missing")]:
        assert _generate(output, deployment, "192.0.2.10").returncode != 0
        assert not output.exists()
