"""Capability-protected loopback bridge for native managed credentials."""

from typing import Annotated, ClassVar

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.services.managed_session import ManagedSession, ManagedSessionStore


class ManagedSessionWrite(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    access_token: str = Field(min_length=1, max_length=16_384)
    expires_at: int = Field(gt=0)
    api_base_url: str = Field(min_length=9, max_length=2_048)


def create_router(store: ManagedSessionStore) -> APIRouter:
    router = APIRouter(prefix="/internal/managed-session", tags=["internal"], include_in_schema=False)

    def require_capability(value: str | None) -> None:
        if not store.authorize(value):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    @router.put("", status_code=status.HTTP_204_NO_CONTENT)
    async def replace_session(  # pyright: ignore[reportUnusedFunction]
        body: ManagedSessionWrite,
        capability: Annotated[str | None, Header(alias="x-managed-session-capability")] = None,
    ) -> Response:
        require_capability(capability)
        try:
            store.replace(
                ManagedSession(
                    access_token=body.access_token,
                    expires_at=body.expires_at,
                    api_base_url=body.api_base_url.rstrip("/"),
                )
            )
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid session") from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("", status_code=status.HTTP_204_NO_CONTENT)
    async def clear_session(  # pyright: ignore[reportUnusedFunction]
        capability: Annotated[str | None, Header(alias="x-managed-session-capability")] = None,
    ) -> Response:
        require_capability(capability)
        store.clear()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


__all__ = ["ManagedSessionWrite", "create_router"]
