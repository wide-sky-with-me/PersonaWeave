import asyncio
import json

from personaweave.comfy import ComfyUIClient
from personaweave.comfy_gateway import ComfyGatewaySettings


async def main() -> None:
    settings = ComfyGatewaySettings()
    if not settings.backend_url:
        raise SystemExit("PERSONAWEAVE_COMFY_BACKEND_URL is not configured")

    client = ComfyUIClient(
        settings.backend_url,
        api_prefix=settings.backend_api_prefix,
        api_key=settings.backend_api_key,
        timeout=settings.request_timeout_seconds,
    )
    nodes = await client.object_info()
    markers = ("face", "reactor", "swap", "insight", "instantid", "identity")
    candidates = []
    for class_type, definition in nodes.items():
        searchable = json.dumps(
            {"class_type": class_type, "definition": definition},
            ensure_ascii=False,
        ).lower()
        if any(marker in searchable for marker in markers):
            candidates.append(class_type)

    print(
        json.dumps(
            {
                "backend": settings.backend_url,
                "api_prefix": settings.backend_api_prefix,
                "node_count": len(nodes),
                "face_related_candidates": sorted(candidates),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
