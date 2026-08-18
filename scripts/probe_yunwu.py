import asyncio
import base64
import json
from io import BytesIO

from PIL import Image, ImageDraw

from personaweave.config import Settings
from personaweave.domain import ValidationRequest
from personaweave.providers.yunwu import YunwuMultimodalProvider


def synthetic_image_uri() -> str:
    image = Image.new("RGB", (192, 192), "white")
    drawing = ImageDraw.Draw(image)
    drawing.ellipse((36, 20, 156, 164), fill=(224, 190, 165), outline="black", width=3)
    drawing.ellipse((70, 72, 82, 84), fill="black")
    drawing.ellipse((110, 72, 122, 84), fill="black")
    drawing.arc((72, 82, 120, 130), start=20, end=160, fill="black", width=3)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


async def main() -> None:
    settings = Settings()
    provider = YunwuMultimodalProvider(
        settings.yunwu_base_url,
        settings.yunwu_api_key,
        settings.yunwu_vision_model,
        settings.request_timeout_seconds,
    )
    image_uri = synthetic_image_uri()
    result = await provider.validate(
        ValidationRequest(
            batch_id="synthetic-probe",
            result_asset_id="synthetic-result",
            source_uri=image_uri,
            result_uri=image_uri,
            expected_face_instance_ids=[],
            protected_face_instance_ids=[],
        )
    )
    expected_checks = {
        "obvious_face_distortion",
        "obvious_identity_mixup",
        "background_or_text_changed",
        "review_confidence",
    }
    if result.result_asset_id != "synthetic-result" or set(result.checks) != expected_checks:
        raise RuntimeError("Yunwu visual QA contract mismatch")
    print(
        json.dumps(
            {
                "provider": result.provider,
                "model": settings.yunwu_vision_model,
                "passed_type": type(result.passed).__name__,
                "check_keys": sorted(result.checks),
                "reason_count": len(result.reasons),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
