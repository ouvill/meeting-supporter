"""Smoke tests for the FastAPI application — no external dependencies.

These tests use ``app.factory.create_openapi_app()`` which builds a lightweight
FastAPI app with stub dependencies.  No soundcard, LLM agents, SQLite, or
Tauri runtime are required.
"""

from app.factory import create_openapi_app
from tests.helpers.api_client import TypedTestClient

app = create_openapi_app()


class TestHealthEndpoint:
    """Verify the health endpoint works via the lightweight app factory."""

    def test_health_returns_200(self) -> None:
        with TypedTestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
