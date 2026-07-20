from fastapi import FastAPI

from app.api.managed_session import create_router
from app.services.managed_session import ManagedSessionStore
from tests.helpers.api_client import TypedTestClient

CAPABILITY = "managed-session-test-capability-000000"


def test_session_bridge_requires_capability_and_never_exposes_tokens() -> None:
    store = ManagedSessionStore(CAPABILITY)
    app = FastAPI()
    app.include_router(create_router(store))
    client = TypedTestClient(app)
    body = {
        "access_token": "secret-access-token",
        "expires_at": 1_900_000_000,
        "api_base_url": "https://managed.example",
    }

    assert client.put("/internal/managed-session", json=body).status_code == 403
    response = client.put(
        "/internal/managed-session",
        json=body,
        headers={"x-managed-session-capability": CAPABILITY},
    )
    assert response.status_code == 204
    assert response.content == b""
    session = store.get()
    assert session is not None
    assert session.access_token == "secret-access-token"
    assert "/internal/managed-session" not in app.openapi()["paths"]

    response = client.delete(
        "/internal/managed-session",
        headers={"x-managed-session-capability": CAPABILITY},
    )
    assert response.status_code == 204
    assert store.get() is None
