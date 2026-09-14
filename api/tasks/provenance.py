"""Bind current refinement inputs to authoritative local batch results without leaking request payloads."""

import hashlib

from core.image_security import resolve_managed_url


def batch_provenance(manager, swapped_url):
    """Bind a result to its server original, including aliases of the same controlled managed file."""
    if manager is None:
        return None
    results = [
        (task, result)
        for task in manager.tasks.values()
        for result in task.results
        if result.status in {"SUCCESS", "PARTIAL"} and result.processed_url
    ]
    selected = next(((task, result) for task, result in results if result.processed_url == swapped_url), None)
    if selected is None:
        requested_path = resolve_managed_url(swapped_url)
        if requested_path is None:
            return None
        for task, result in results:
            try:
                result_path = resolve_managed_url(result.processed_url)
            except ValueError:
                # An unrelated cleared historical result cannot invalidate this request.
                continue
            if requested_path == result_path:
                selected = task, result
                break
    if selected is None:
        return None
    task, result = selected
    request = manager._requests.get(task.task_id)
    mappings = (request.person_mappings or [request]) if request is not None else []
    sources = [
        {
            "person_index": getattr(mapping, "person_index", None),
            "source_identity_id": mapping.source_identity_id,
            "source_asset_ids": mapping.source_asset_ids,
            "manual_count": len(mapping.source_manual_images),
            "manual_sha256": [hashlib.sha256(value.encode()).hexdigest() for value in mapping.source_manual_images],
        }
        for mapping in mappings
    ]
    return {
        "lineage": "batch-local",
        "batch_task_id": task.task_id,
        "client_target_id": result.client_target_id,
        "original_url": result.original_url
        if result.original_url and result.original_url.startswith("/api/v1/files/")
        else None,
        "original_provenance": "server-batch-result",
        "parameters": request.parameters.model_dump(mode="json") if request is not None else {},
        "sources": sources,
        "local_model_routing": result.model_routing,
    }
