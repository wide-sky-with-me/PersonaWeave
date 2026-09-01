"""
Model downloader for PersonaWeave.
Downloads required ONNX models:
- buffalo_l (SCRFD 10G face detector + ArcFace w600k_r50 recognizer)
- inswapper_128.onnx (Face swap model)
- xseg_1.onnx (face occlusion model)
- bisenet_resnet_34.onnx (semantic face-region parsing, e.g. glasses protection)
"""

import hashlib
import os
import urllib.request
import zipfile

from tqdm import tqdm

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")

MODEL_URLS = {
    "buffalo_l.zip": "https://huggingface.co/public-data/insightface/resolve/main/models/buffalo_l.zip",
    "inswapper_128.onnx": "https://huggingface.co/ezioruan/inswapper_128.onnx/resolve/main/inswapper_128.onnx",
    "xseg_1.onnx": "https://huggingface.co/facefusion/models-3.1.0/resolve/main/xseg_1.onnx",
    "bisenet_resnet_34.onnx": "https://huggingface.co/facefusion/models-3.0.0/resolve/main/bisenet_resnet_34.onnx",
}

# Hashes were verified against the configured sources and the ONNX assets used
# by the regression environment on 2026-08-31. Never accept a same-name model
# solely because its byte size looks plausible.
MODEL_SHA256 = {
    "buffalo_l.zip": "80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f",
    "buffalo_l/det_10g.onnx": "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    "buffalo_l/w600k_r50.onnx": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
    "buffalo_l/2d106det.onnx": "f001b856447c413801ef5c42091ed0cd516fcd21f2d6b79635b1e733a7109dbf",
    "inswapper_128.onnx": "e4a3f08c753cb72d04e10aa0f7dbe3deebbf39567d4ead6dce08e98aa49e16af",
    "xseg_1.onnx": "c4d1498b8a03b5fe2a3a5d2ef2a0402ab03bd51edaf5b2d8d5fb764702a97dd3",
    "bisenet_resnet_34.onnx": "4a0b8c958a3c938913bd06a8365dbb3c8761afba6ecbf0d14b3b1f77eb230c96",
}

# Reject empty/text placeholders while still allowing the downloader to resume with
# model files obtained from another trusted distribution channel.
MIN_MODEL_BYTES = 1024


class DownloadProgressBar(tqdm):
    """Progress bar for urllib downloads."""

    def update_to(self, b=1, bsize=1, tsize=None):
        if tsize is not None:
            self.total = tsize
        self.update(b * bsize - self.n)


def is_valid_model(path: str, expected_sha256: str | None = None) -> bool:
    """Return whether a model has a plausible size and the expected digest."""
    if not os.path.isfile(path) or os.path.getsize(path) < MIN_MODEL_BYTES:
        return False
    if expected_sha256 is None:
        return True
    digest = hashlib.sha256()
    with open(path, "rb") as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected_sha256


def safe_extract_zip(archive: zipfile.ZipFile, destination: str) -> None:
    """Extract a ZIP archive only when every member remains below destination."""
    destination_path = os.path.realpath(destination)
    for member in archive.infolist():
        member_path = os.path.realpath(os.path.join(destination_path, member.filename))
        if os.path.commonpath((destination_path, member_path)) != destination_path:
            raise ValueError(f"Unsafe ZIP member path: {member.filename}")
    archive.extractall(destination_path)


def ensure_model(filename: str) -> None:
    """Download a missing model and replace incomplete local placeholders."""
    output_path = os.path.join(MODEL_DIR, filename)
    if is_valid_model(output_path, MODEL_SHA256[filename]):
        return
    if os.path.exists(output_path):
        print(f"Replacing invalid model placeholder: {output_path}")
        os.remove(output_path)
    download_file(MODEL_URLS[filename], output_path)
    if not is_valid_model(output_path, MODEL_SHA256[filename]):
        raise RuntimeError(f"Downloaded model failed SHA-256 verification: {filename}")


def download_file(url: str, output_path: str):
    """Download a file with visual progress bar."""
    print(f"Downloading {os.path.basename(output_path)} from {url}...")
    with DownloadProgressBar(unit="B", unit_scale=True, miniters=1, desc=os.path.basename(output_path)) as t:
        urllib.request.urlretrieve(url, filename=output_path, reporthook=t.update_to)


def setup_models():
    """Ensure all required model files are downloaded and extracted."""
    os.makedirs(MODEL_DIR, exist_ok=True)

    # Download and extract InsightFace's detector, recognizer, and landmark models.
    buffalo_zip = os.path.join(MODEL_DIR, "buffalo_l.zip")
    buffalo_dir = os.path.join(MODEL_DIR, "buffalo_l")
    detector_path = os.path.join(buffalo_dir, "det_10g.onnx")
    recognizer_path = os.path.join(buffalo_dir, "w600k_r50.onnx")
    landmark_path = os.path.join(buffalo_dir, "2d106det.onnx")
    buffalo_assets = {
        detector_path: MODEL_SHA256["buffalo_l/det_10g.onnx"],
        recognizer_path: MODEL_SHA256["buffalo_l/w600k_r50.onnx"],
        landmark_path: MODEL_SHA256["buffalo_l/2d106det.onnx"],
    }
    if not all(is_valid_model(path, digest) for path, digest in buffalo_assets.items()):
        if not is_valid_model(buffalo_zip, MODEL_SHA256["buffalo_l.zip"]):
            if os.path.exists(buffalo_zip):
                os.remove(buffalo_zip)
            download_file(MODEL_URLS["buffalo_l.zip"], buffalo_zip)
        print("Extracting buffalo_l.zip...")
        os.makedirs(buffalo_dir, exist_ok=True)
        with zipfile.ZipFile(buffalo_zip, "r") as zip_ref:
            safe_extract_zip(zip_ref, buffalo_dir)
        if not all(is_valid_model(path, digest) for path, digest in buffalo_assets.items()):
            raise RuntimeError(
                "buffalo_l archive did not contain the required detector, recognizer, and landmark models"
            )
        print("buffalo_l extracted successfully.")

    # These filenames match the paths consumed by PersonaWeavePipeline.
    for filename in (
        "inswapper_128.onnx",
        "xseg_1.onnx",
        "bisenet_resnet_34.onnx",
    ):
        ensure_model(filename)
    print("\nAll models ready in:", MODEL_DIR)
    for root, _, files in os.walk(MODEL_DIR):
        for f in files:
            p = os.path.join(root, f)
            print(f"  - {os.path.relpath(p, MODEL_DIR)} ({os.path.getsize(p) / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    setup_models()
