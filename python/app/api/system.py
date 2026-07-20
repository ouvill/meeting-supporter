"""System endpoints: health, root, devices."""

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.types import InputDevice


def create_router(*, get_input_devices: Callable[[], list[InputDevice]]) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse({"status": "ok"})

    @router.get("/")
    async def root() -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse({"status": "ok", "message": "会議支援AI バックエンド起動中"})

    @router.get("/devices")
    async def list_devices() -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse(get_input_devices())

    return router


__all__ = ["create_router"]
