from app.factory import create_openapi_app
from tests.helpers.api_client import TypedTestClient


def test_minutes_generation_is_not_exposed() -> None:
    with TypedTestClient(create_openapi_app()) as client:
        response = client.post("/meetings/synthetic-meeting/minutes")
    assert response.status_code == 404
