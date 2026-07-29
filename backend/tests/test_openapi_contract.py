"""Public HTTP route-surface regression contract."""

from __future__ import annotations

import hashlib

from papercreator.api.app import app


EXPECTED_PATHS = 157
EXPECTED_OPERATIONS = 181
EXPECTED_ROUTE_SHA256 = "326066d7485f6377a5cf8b60f38476910d6b9420a2768bacf6fdc26e8534e9c6"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def test_openapi_route_surface_is_changed_deliberately():
    schema = app.openapi()
    rows = sorted(
        f"{method.upper()} {path}"
        for path, item in schema["paths"].items()
        for method in item
        if method.lower() in HTTP_METHODS
    )
    operation_ids = [
        operation.get("operationId")
        for item in schema["paths"].values()
        for method, operation in item.items()
        if method.lower() in HTTP_METHODS
    ]

    assert len(schema["paths"]) == EXPECTED_PATHS
    assert len(rows) == EXPECTED_OPERATIONS
    assert None not in operation_ids
    assert len(operation_ids) == len(set(operation_ids)), "OpenAPI operation IDs must be unique"
    digest = hashlib.sha256(("\n".join(rows) + "\n").encode()).hexdigest()
    route_listing = "\n".join(rows)
    assert digest == EXPECTED_ROUTE_SHA256, (
        "The public route surface changed. Review API/types/UI/docs compatibility, then "
        f"update this snapshot deliberately.\nCurrent routes:\n{route_listing}"
    )


def test_private_desktop_shutdown_route_stays_out_of_openapi():
    assert "/api/system/shutdown" not in app.openapi()["paths"]
    assert any(
        getattr(route, "path", "") == "/api/system/shutdown"
        for route in app.routes
    ), "the owned-desktop graceful shutdown route must still exist"
