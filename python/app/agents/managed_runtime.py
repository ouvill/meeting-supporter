"""Managed reply adapter backed by the authenticated Worker relay."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import cast, final, override
from uuid import uuid4

import httpx

from app.agents.models import ReplyAgentRuntime, ReplyPrompt
from app.agents.route_catalog import RouteProbeStatus
from app.core.config import RouteAction, RouteReadiness
from app.core.protocols import StreamLike
from app.services.managed_session import ManagedSessionStore


def _json_object(raw: str | bytes) -> dict[str, object]:
    value = cast(object, json.loads(raw))
    if not isinstance(value, dict):
        raise TypeError("expected JSON object")
    return cast(dict[str, object], value)


@final
class ManagedRuntimeError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@final
class ManagedReplyStream(StreamLike):
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @override
    async def stream_text(self, *, delta: bool) -> AsyncIterator[str]:
        if not delta:
            raise ValueError("managed reply stream supports delta mode only")
        event: str | None = None
        async for line in self._response.aiter_lines():
            if line.startswith("event:"):
                event = line.removeprefix("event:").strip()
                continue
            if not line.startswith("data:") or event is None:
                continue
            try:
                payload = _json_object(line.removeprefix("data:").strip())
            except (json.JSONDecodeError, TypeError) as error:
                raise ManagedRuntimeError("INVALID_MANAGED_RESPONSE") from error
            if event == "delta":
                text = payload.get("text")
                if not isinstance(text, str):
                    raise ManagedRuntimeError("INVALID_MANAGED_RESPONSE")
                yield text
            elif event == "error":
                code = payload.get("code")
                raise ManagedRuntimeError(code if isinstance(code, str) else "PROVIDER_UNAVAILABLE")
            elif event == "done":
                return
            event = None
        raise ManagedRuntimeError("PROVIDER_UNAVAILABLE")


@final
@dataclass(frozen=True)
class ManagedReplyAgentRuntime(ReplyAgentRuntime):
    session_store: ManagedSessionStore
    instruction: str

    @override
    def run_stream(self, prompt: ReplyPrompt) -> AbstractAsyncContextManager[StreamLike]:
        return self._run_stream(prompt)

    @asynccontextmanager
    async def _run_stream(self, prompt: ReplyPrompt) -> AsyncGenerator[StreamLike]:
        session = self.session_store.get()
        if session is None or session.expires_at <= int(time.time()) + 5:
            raise ManagedRuntimeError("AUTH_REQUIRED")
        timeout = httpx.Timeout(connect=10, read=30, write=10, pool=10)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                async with client.stream(
                    "POST",
                    f"{session.api_base_url}/v1/llm/reply",
                    headers={
                        "authorization": f"Bearer {session.access_token}",
                        "accept": "text/event-stream",
                        "content-type": "application/json",
                    },
                    json={
                        "request_id": f"reply_{uuid4().hex}",
                        "prompt": prompt.text,
                        "instruction": self.instruction,
                    },
                ) as response:
                    if response.status_code != 200:
                        try:
                            payload = await response.aread()
                            code = _json_object(payload).get("code")
                        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                            code = None
                        raise ManagedRuntimeError(code if isinstance(code, str) else "PROVIDER_UNAVAILABLE")
                    yield ManagedReplyStream(response)
        except httpx.HTTPError as error:
            raise ManagedRuntimeError("PROVIDER_UNAVAILABLE") from error


async def probe_managed_route_status(session_store: ManagedSessionStore) -> RouteProbeStatus:
    session = session_store.get()
    if session is None or session.expires_at <= int(time.time()) + 5:
        return RouteProbeStatus(
            readiness="setup_required",
            reason_code="AUTH_REQUIRED",
            message="Google または Microsoft でログインしてください。",
            action="sign_in",
        )
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get(
                f"{session.api_base_url}/v1/entitlement",
                headers={"authorization": f"Bearer {session.access_token}"},
            )
    except httpx.HTTPError:
        return RouteProbeStatus(
            readiness="error",
            reason_code="MANAGED_SERVICE_UNAVAILABLE",
            message="利用状況を確認できませんでした。",
            action="retry",
        )
    if response.status_code == 401:
        return RouteProbeStatus(
            readiness="setup_required",
            reason_code="AUTH_REQUIRED",
            message="ログインを更新してください。",
            action="sign_in",
        )
    if response.status_code != 200:
        return RouteProbeStatus(
            readiness="error",
            reason_code="MANAGED_SERVICE_UNAVAILABLE",
            message="利用状況を確認できませんでした。",
            action="retry",
        )
    try:
        payload = _json_object(response.content)
        managed_value = payload.get("managed")
        if not isinstance(managed_value, dict):
            raise TypeError("invalid managed entitlement")
        managed = cast(dict[str, object], managed_value)
        readiness = managed.get("readiness")
        reason = managed.get("reason")
        reply_value = managed.get("reply")
        if not isinstance(reply_value, dict):
            raise TypeError("invalid managed reply entitlement")
        reply_selectable = cast(dict[str, object], reply_value).get("selectable")
        if not isinstance(readiness, str) or not isinstance(reason, str) or not isinstance(reply_selectable, bool):
            raise TypeError("invalid managed entitlement fields")
    except (json.JSONDecodeError, TypeError, ValueError):
        return RouteProbeStatus(
            readiness="error",
            reason_code="INVALID_MANAGED_RESPONSE",
            message="利用状況を確認できませんでした。",
            action="retry",
        )
    if readiness == "ready" and reply_selectable is not True:
        return RouteProbeStatus(
            readiness="unavailable",
            reason_code="SERVICE_DISABLED",
            message="現在、Meeting Supporter AIの返答案を利用できません。",
            action="retry",
            service_tier="standard",
        )
    mapping: dict[str, tuple[RouteReadiness, str, RouteAction]] = {
        "ready": ("ready", "利用できます。", "none"),
        "subscription_required": ("setup_required", "月額プランの契約が必要です。", "subscribe"),
        "payment_required": ("setup_required", "支払い方法を確認してください。", "manage_billing"),
        "quota_exhausted": ("unavailable", "今月の共通利用枠を使い切りました。", "view_usage"),
        "service_disabled": ("unavailable", "現在この機能を利用できません。", "retry"),
        "unavailable": ("unavailable", "アカウントを削除しています。", "none"),
    }
    route_status = mapping.get(readiness)
    if route_status is None:
        return RouteProbeStatus(
            readiness="error",
            reason_code="INVALID_MANAGED_RESPONSE",
            message="利用状況を確認できませんでした。",
            action="retry",
        )
    route_readiness, message, action = route_status
    return RouteProbeStatus(
        readiness=route_readiness,
        reason_code=reason,
        message=message,
        action=action,
        service_tier="standard",
    )


__all__ = ["ManagedReplyAgentRuntime", "ManagedRuntimeError", "probe_managed_route_status"]
