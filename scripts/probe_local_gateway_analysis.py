import asyncio
import base64
import json
import mimetypes
import os
import sys
from pathlib import Path
from uuid import uuid4

import httpx


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: probe_local_gateway_analysis.py IMAGE_PATH")
    image_path = Path(sys.argv[1]).resolve()
    if not image_path.is_file():
        raise SystemExit(f"image does not exist: {image_path}")
    mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    gateway_url = os.getenv("PERSONAWEAVE_COMFY_GATEWAY_URL", "http://127.0.0.1:8010").rstrip("/")
    request = {
        "batch_id": "local-gateway-probe",
        "asset_id": "local-gateway-probe-image",
        "asset_uri": f"data:{mime_type};base64,{encoded}",
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            f"{gateway_url}/v1/analyze-faces",
            headers={"Idempotency-Key": f"probe-{uuid4()}"},
            json=request,
        )
        response.raise_for_status()
        result = response.json()
    faces = result.get("faces")
    if not isinstance(faces, list):
        raise RuntimeError(f"gateway response omitted faces: {result}")
    if any(face.get("needs_confirmation") is not True for face in faces):
        raise RuntimeError(f"gateway response bypassed confirmation: {result}")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
