import asyncio
import json
import mimetypes
import sys
from pathlib import Path

from personaweave.comfy import ComfyUIClient, WorkflowOutput
from personaweave.comfy_gateway import ComfyGatewaySettings


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: probe_comfy_face_analysis.py IMAGE_PATH")
    image_path = Path(sys.argv[1]).resolve()
    if not image_path.is_file():
        raise SystemExit(f"image does not exist: {image_path}")

    settings = ComfyGatewaySettings()
    if not settings.backend_url:
        raise SystemExit("PERSONAWEAVE_COMFY_BACKEND_URL is not configured")
    client = ComfyUIClient(
        settings.backend_url,
        api_prefix=settings.backend_api_prefix,
        api_key=settings.backend_api_key,
        timeout=settings.request_timeout_seconds,
    )
    mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    uploaded = await client.upload_image(
        image_path.read_bytes(),
        mime_type,
        image_path.name,
        "personaweave/probe",
    )
    request = json.dumps({"asset_id": "local-face-analysis-probe"}, separators=(",", ":"))
    prompt = {
        "1": {"class_type": "LoadImage", "inputs": {"image": uploaded}},
        "2": {
            "class_type": "LoadMediaPipeFaceLandmarker",
            "inputs": {"model_name": "mediapipe_face_fp32.safetensors"},
        },
        "3": {
            "class_type": "MediaPipeFaceLandmarker",
            "inputs": {
                "face_detection_model": ["2", 0],
                "image": ["1", 0],
                "detector_variant": "both",
                "num_faces": 0,
                "min_confidence": 0.35,
                "missing_frame_fallback": "empty",
            },
        },
        "4": {
            "class_type": "PersonaWeaveFaceAnalysisJson",
            "inputs": {"bboxes": ["3", 1], "request_json": request},
        },
        "5": {"class_type": "PersonaWeaveJsonOutput", "inputs": {"text": ["4", 0]}},
    }
    _, history = await client.execute(prompt)
    analysis, _ = await client.read_output(
        history,
        WorkflowOutput(node_id="5", field="text", kind="json"),
    )
    faces = analysis.get("faces")
    if not isinstance(faces, list):
        raise RuntimeError(f"face analysis omitted faces: {analysis}")
    if any(face.get("needs_confirmation") is not True for face in faces):
        raise RuntimeError(f"face analysis bypassed confirmation: {analysis}")
    print(json.dumps(analysis, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
