from __future__ import annotations

import importlib
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_cli_overrides_are_passed_to_the_application(temp_home, monkeypatch):
    import uvicorn

    from papercreator import __main__ as cli
    app_module = importlib.import_module("papercreator.api.app")

    captured: dict[str, object] = {}
    fake_app = SimpleNamespace(state=SimpleNamespace())

    def fake_create_app(*, bind_host: str, bind_port: int):
        captured["bind"] = (bind_host, bind_port)
        return fake_app

    class FakeConfig:
        def __init__(self, application, **kwargs):
            captured["application"] = application
            captured["uvicorn"] = kwargs

    class FakeServer:
        def __init__(self, config):
            self.config = config
            self.should_exit = False

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr(app_module, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)

    assert cli.main(["--host", "127.0.0.9", "--port", "9123"]) == 0
    assert captured["bind"] == ("127.0.0.9", 9123)
    assert captured["application"] is fake_app
    assert captured["uvicorn"] == {
        "host": "127.0.0.9",
        "port": 9123,
        "log_level": "info",
        "log_config": None,
        "timeout_graceful_shutdown": 2,
    }
    assert captured["ran"] is True
    assert callable(fake_app.state.request_shutdown)


def test_effective_bind_endpoint_drives_log_and_cors(temp_home, monkeypatch):
    app_module = importlib.import_module("papercreator.api.app")

    info_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        app_module.log,
        "info",
        lambda *args, **_kwargs: info_calls.append(args),
    )
    application = app_module.create_app(bind_host="127.0.0.9", bind_port=9123)

    with TestClient(application) as client:
        response = client.options(
            "/api/health",
            headers={
                "Origin": "http://127.0.0.9:9123",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.9:9123"
    assert ("listening on http://%s:%s", "127.0.0.9", 9123) in info_calls
