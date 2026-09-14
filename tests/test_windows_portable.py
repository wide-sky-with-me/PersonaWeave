"""Portable-launcher isolation and offline packaging regressions."""

import asyncio

import pytest
from dotenv import dotenv_values

from api.portable import configure, instance_lock
from api.routes.system import get_sample_manifest
from scripts.build_windows_portable import check_public_asset, portable_requirements, trim_development_files


@pytest.mark.parametrize(
    "path", [".env", "data/portable-runtime.json", "data/projects.sqlite3", "data/private.png", "outputs/result.png"]
)
def test_build_rejects_credentials_and_prior_runtime_data(path):
    with pytest.raises(ValueError, match="private data"):
        check_public_asset(path)


def test_cpu_requirements_keep_lock_pins_without_linux_gpu_packages():
    exported = "# generated\nnumpy==2.5.2 ; python_version >= '3.12'\nonnxruntime-gpu==1.20.2\nnvidia-cudnn-cu12==9.0\nopencv-python==5.0\nopencv-python-headless==5.0\n"
    result = portable_requirements(exported)
    assert "numpy==2.5.2 ; python_version >= '3.12'" in result
    assert "onnxruntime==" in result
    assert "nvidia-" not in result
    assert "onnxruntime-gpu" not in result
    assert "opencv-python==" not in result
    assert "opencv-python-headless==5.0" in result


def test_gpu_requirements_keep_nvidia_pins_and_exclude_cpu_runtime():
    result = portable_requirements(
        "onnxruntime-gpu==1.20.2\nnvidia-cudnn-cu12==9.24.0.43\nonnxruntime==1.23.2\n", "gpu"
    )
    assert "nvidia-cudnn-cu12==9.24.0.43" in result
    assert "onnxruntime-gpu==1.23.2" in result
    assert "onnxruntime==" not in result
    assert "1.20.2" not in result


def test_trimming_preserves_runtime_dlls_and_licenses(tmp_path):
    package = tmp_path / "Lib/site-packages/nvidia/cublas"
    package.mkdir(parents=True)
    for name in ("runtime.dll", "LICENSE", "library.lib", "header.h", "cache.pyc"):
        (package / name).write_bytes(b"test")
    assert trim_development_files(tmp_path) == 12
    assert sorted(path.name for path in package.iterdir()) == ["LICENSE", "runtime.dll"]


def test_second_launcher_cannot_open_same_database(tmp_path):
    with instance_lock(tmp_path) as first:
        assert first
        with instance_lock(tmp_path) as second:
            assert not second
    with instance_lock(tmp_path) as restarted:
        assert restarted


def test_ai_configuration_preserves_blanks_and_hides_keys(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text('AI_VISION_API_KEY="test-existing-key"\n', encoding="utf-8")
    inputs = iter(["openai/test-vision", "https://example.com/v1", "openai/test-image", "https://example.com/v1"])
    secrets = iter(["", 'test-new-"quoted-key'])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("api.portable.getpass.getpass", lambda _: next(secrets))
    configure(tmp_path)
    values = dotenv_values(tmp_path / ".env")
    assert values["AI_VISION_API_KEY"] == "test-existing-key"
    assert values["AI_IMAGE_API_KEY"] == 'test-new-"quoted-key'
    assert "test-existing-key" not in capsys.readouterr().out
    assert not (tmp_path / ".env.tmp").exists()


def test_sample_manifest_only_advertises_shipped_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "data/test_target"
    directory.mkdir(parents=True)
    (directory / "01.png").write_bytes(b"fixture")
    result = asyncio.run(get_sample_manifest())
    assert [item["path"] for item in result["sources"]] == ["data/test_target/01.png"]
    assert result["targets"] == []
