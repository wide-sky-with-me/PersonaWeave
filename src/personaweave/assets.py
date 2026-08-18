import base64
import binascii
import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse


class UnsafeAssetPath(ValueError):
    pass


class AssetResolver:
    def __init__(self, asset_root: Path):
        self.asset_root = asset_root.resolve()

    def local_path(self, uri: str) -> Path | None:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            return None
        path = Path(unquote(parsed.path)).resolve()
        if not path.is_relative_to(self.asset_root):
            raise UnsafeAssetPath("local asset is outside the configured asset root")
        return path

    def for_remote(self, uri: str, media_type: str | None = None) -> str:
        path = self.local_path(uri)
        if path is None:
            return uri
        detected_type = media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{detected_type};base64,{encoded}"

    def materialize_result(
        self,
        batch_id: str,
        source_asset_id: str,
        uri: str,
    ) -> tuple[str, str]:
        if not uri.startswith("data:"):
            return uri, "remote-unverified"
        try:
            header, encoded = uri.split(",", 1)
            media_type = header[5:].split(";", 1)[0]
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("provider returned an invalid data URI") from error
        extension = mimetypes.guess_extension(media_type) or ".bin"
        digest = hashlib.sha256(payload).hexdigest()
        directory = self.asset_root / batch_id / "results"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{source_asset_id}-{digest[:12]}{extension}"
        path.write_bytes(payload)
        return path.resolve().as_uri(), digest

