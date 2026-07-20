"""Tests for app.api.system — health, root, devices."""

from fastapi import FastAPI

from app.api.system import create_router
from app.core.types import InputDevice
from tests.helpers.api_client import TypedTestClient


def _make_client(devices: list[InputDevice] | None = None) -> TypedTestClient:
    app = FastAPI()
    router = create_router(get_input_devices=lambda: devices or [])
    app.include_router(router)
    return TypedTestClient(app)


class TestHealth:
    def test_health_returns_ok(self) -> None:
        client = _make_client()
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestRoot:
    def test_root_returns_status_and_message(self) -> None:
        client = _make_client()
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json_object()
        assert data["status"] == "ok"
        assert "バックエンド起動中" in str(data["message"])


class TestDevices:
    def test_devices_returns_empty_list(self) -> None:
        client = _make_client()
        resp = client.get("/devices")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_devices_returns_device_list(self) -> None:
        devices: list[InputDevice] = [
            {
                "index": 0,
                "name": "Built-in Microphone",
                "is_monitor": False,
                "is_default": True,
                "hostapi": "",
                "capture": "soundcard",
            },
            {
                "index": 1,
                "name": "Monitor of Built-in Speaker",
                "is_monitor": True,
                "hostapi": "",
                "is_default": False,
                "capture": "soundcard",
            },
        ]
        client = _make_client(devices)
        resp = client.get("/devices")
        assert resp.status_code == 200
        data = resp.json_array()
        assert len(data) == 2
        first = data[0]
        second = data[1]
        assert isinstance(first, dict)
        assert isinstance(second, dict)
        assert first["name"] == "Built-in Microphone"
        assert second["is_monitor"] is True
        assert first["is_default"] is True
