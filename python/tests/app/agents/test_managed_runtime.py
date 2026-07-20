import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import cast, override

import httpx
import pytest

from app.agents.managed_runtime import (
    ManagedReplyAgentRuntime,
    ManagedReplyStream,
    ManagedRuntimeError,
    probe_managed_route_status,
)
from app.agents.models import ReplyPrompt
from app.agents.route_catalog import RouteProbeStatus
from app.services.managed_session import ManagedSession, ManagedSessionStore


class _ManagedHttpMock:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> None:
        self.request: httpx.Request | None = None
        self.response: httpx.Response | None = None
        self.timeout: httpx.Timeout | None = None
        self.follow_redirects: bool | None = None
        real_client = httpx.AsyncClient

        def capture(request: httpx.Request) -> httpx.Response:
            self.request = request
            response = handler(request)
            self.response = response
            return response

        transport = httpx.MockTransport(capture)

        def client(
            *,
            timeout: float | httpx.Timeout,
            follow_redirects: bool,
        ) -> httpx.AsyncClient:
            self.timeout = timeout if isinstance(timeout, httpx.Timeout) else httpx.Timeout(timeout)
            self.follow_redirects = follow_redirects
            return real_client(
                transport=transport,
                timeout=timeout,
                follow_redirects=follow_redirects,
            )

        monkeypatch.setattr(httpx, "AsyncClient", client)


def _sse_body(*events: tuple[str, str]) -> bytes:
    return "".join(f"event: {event}\ndata: {data}\n\n" for event, data in events).encode()


def _session_store(*, expires_at: int | None = None) -> ManagedSessionStore:
    store = ManagedSessionStore("c" * 32)
    store.replace(
        ManagedSession(
            access_token="secret-token",
            expires_at=expires_at if expires_at is not None else int(time.time()) + 300,
            api_base_url="https://managed.example",
        )
    )
    return store


def _runtime(store: ManagedSessionStore | None = None) -> ManagedReplyAgentRuntime:
    return ManagedReplyAgentRuntime(
        session_store=store if store is not None else _session_store(),
        instruction="Answer briefly.",
    )


async def _collect_runtime(runtime: ManagedReplyAgentRuntime) -> list[str]:
    async with runtime.run_stream(ReplyPrompt(text="What should I say?")) as stream:
        return [chunk async for chunk in stream.stream_text(delta=True)]


async def _collect_stream(stream: ManagedReplyStream, *, delta: bool = True) -> list[str]:
    return [chunk async for chunk in stream.stream_text(delta=delta)]


def test_managed_reply_stream_returns_exact_delta_chunks_and_ignores_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _sse_body(
        ("delta", '{"text":"first"}'),
        ("usage", '{"input_tokens":10,"output_tokens":2}'),
        ("delta", '{"text":" second"}'),
        ("done", "{}"),
    )
    http = _ManagedHttpMock(monkeypatch, lambda request: httpx.Response(200, request=request, content=body))

    chunks = asyncio.run(_collect_runtime(_runtime()))

    assert chunks == ["first", " second"]
    assert http.response is not None and http.response.is_closed


class _FailAfterDoneStream(httpx.AsyncByteStream):
    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _sse_body(("done", "{}"))
        raise AssertionError("stream read after done event")

    @override
    async def aclose(self) -> None:
        return None


def test_managed_reply_stream_stops_reading_after_done() -> None:
    request = httpx.Request("POST", "https://managed.example/v1/llm/reply")
    response = httpx.Response(200, request=request, stream=_FailAfterDoneStream())

    assert asyncio.run(_collect_stream(ManagedReplyStream(response))) == []


def test_managed_reply_stream_rejects_non_delta_mode() -> None:
    response = httpx.Response(200, content=_sse_body(("done", "{}")))

    with pytest.raises(ValueError, match="^managed reply stream supports delta mode only$"):
        _ = asyncio.run(_collect_stream(ManagedReplyStream(response), delta=False))


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ('{"code":"RATE_LIMITED"}', "RATE_LIMITED"),
        ("{}", "PROVIDER_UNAVAILABLE"),
    ],
)
def test_managed_reply_stream_propagates_error_event_code(payload: str, expected_code: str) -> None:
    response = httpx.Response(200, content=_sse_body(("error", payload)))

    with pytest.raises(ManagedRuntimeError) as raised:
        _ = asyncio.run(_collect_stream(ManagedReplyStream(response)))

    assert raised.value.code == expected_code


@pytest.mark.parametrize(
    "body",
    [
        _sse_body(("delta", "{")),
        _sse_body(("delta", "[]")),
        _sse_body(("delta", "{}")),
    ],
    ids=["malformed-json", "non-object", "missing-delta-text"],
)
def test_managed_reply_stream_rejects_invalid_delta_payload(body: bytes) -> None:
    response = httpx.Response(200, content=body)

    with pytest.raises(ManagedRuntimeError) as raised:
        _ = asyncio.run(_collect_stream(ManagedReplyStream(response)))

    assert raised.value.code == "INVALID_MANAGED_RESPONSE"


def test_managed_reply_stream_rejects_eof_without_done() -> None:
    response = httpx.Response(200, content=_sse_body(("delta", '{"text":"partial"}')))

    with pytest.raises(ManagedRuntimeError) as raised:
        _ = asyncio.run(_collect_stream(ManagedReplyStream(response)))

    assert raised.value.code == "PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize("has_session", [False, True], ids=["missing", "expiry-boundary"])
def test_managed_runtime_rejects_invalid_session_without_http(
    monkeypatch: pytest.MonkeyPatch,
    has_session: bool,
) -> None:
    store = _session_store(expires_at=int(time.time()) + 5) if has_session else ManagedSessionStore("c" * 32)
    http = _ManagedHttpMock(
        monkeypatch,
        lambda request: httpx.Response(500, request=request),
    )

    with pytest.raises(ManagedRuntimeError) as raised:
        _ = asyncio.run(_collect_runtime(_runtime(store)))

    assert raised.value.code == "AUTH_REQUIRED"
    assert http.request is None


def test_managed_runtime_posts_wire_contract_without_token_in_body(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _ManagedHttpMock(
        monkeypatch,
        lambda request: httpx.Response(200, request=request, content=_sse_body(("done", "{}"))),
    )

    assert asyncio.run(_collect_runtime(_runtime())) == []

    request = cast(httpx.Request, http.request)
    assert request.method == "POST"
    assert str(request.url) == "https://managed.example/v1/llm/reply"
    assert request.headers["authorization"] == "Bearer secret-token"
    assert request.headers["accept"] == "text/event-stream"
    assert request.headers["content-type"] == "application/json"
    assert http.follow_redirects is False
    assert http.timeout is not None
    assert (http.timeout.connect, http.timeout.read, http.timeout.write, http.timeout.pool) == (10, 30, 10, 10)
    payload = cast(dict[str, object], json.loads(request.content))
    assert re.fullmatch(r"reply_[0-9a-f]{32}", cast(str, payload["request_id"]))
    assert payload["prompt"] == "What should I say?"
    assert payload["instruction"] == "Answer briefly."
    assert "secret-token" not in request.content.decode()
    assert http.response is not None and http.response.is_closed


@pytest.mark.parametrize(
    ("status_code", "code"),
    [
        (401, "AUTH_REQUIRED"),
        (402, "PAYMENT_REQUIRED"),
        (403, "ENTITLEMENT_REQUIRED"),
        (429, "QUOTA_EXHAUSTED"),
        (429, "RATE_LIMITED"),
        (503, "PROVIDER_UNAVAILABLE"),
        (503, "SERVICE_DISABLED"),
        (400, "BAD_REQUEST"),
        (404, "NOT_FOUND"),
        (500, "INTERNAL_ERROR"),
    ],
)
def test_managed_runtime_propagates_non_success_codes(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    code: str,
) -> None:
    http = _ManagedHttpMock(
        monkeypatch,
        lambda request: httpx.Response(status_code, request=request, json={"code": code}),
    )

    with pytest.raises(ManagedRuntimeError) as raised:
        _ = asyncio.run(_collect_runtime(_runtime()))

    assert raised.value.code == code
    assert http.response is not None and http.response.is_closed


@pytest.mark.parametrize(
    "body",
    [b"{", b"[]", b'{"code":1}', b"{}"],
    ids=["malformed-json", "non-object", "non-string-code", "missing-code"],
)
def test_managed_runtime_normalizes_invalid_error_responses(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    http = _ManagedHttpMock(
        monkeypatch,
        lambda request: httpx.Response(500, request=request, content=body),
    )

    with pytest.raises(ManagedRuntimeError) as raised:
        _ = asyncio.run(_collect_runtime(_runtime()))

    assert raised.value.code == "PROVIDER_UNAVAILABLE"
    assert http.response is not None and http.response.is_closed


def test_managed_runtime_normalizes_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("relay unavailable", request=request)

    _ = _ManagedHttpMock(monkeypatch, fail)

    with pytest.raises(ManagedRuntimeError) as raised:
        _ = asyncio.run(_collect_runtime(_runtime()))

    assert raised.value.code == "PROVIDER_UNAVAILABLE"


def test_managed_runtime_closes_response_after_parser_error(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _ManagedHttpMock(
        monkeypatch,
        lambda request: httpx.Response(200, request=request, content=_sse_body(("delta", "{}"))),
    )

    with pytest.raises(ManagedRuntimeError) as raised:
        _ = asyncio.run(_collect_runtime(_runtime()))

    assert raised.value.code == "INVALID_MANAGED_RESPONSE"
    assert http.response is not None and http.response.is_closed


@pytest.mark.parametrize("has_session", [False, True], ids=["missing", "expiry-boundary"])
def test_probe_requires_current_session_without_http(monkeypatch: pytest.MonkeyPatch, has_session: bool) -> None:
    store = _session_store(expires_at=int(time.time()) + 5) if has_session else ManagedSessionStore("c" * 32)
    http = _ManagedHttpMock(monkeypatch, lambda request: httpx.Response(500, request=request))

    status = asyncio.run(probe_managed_route_status(store))

    assert status == RouteProbeStatus(
        readiness="setup_required",
        reason_code="AUTH_REQUIRED",
        message="Google または Microsoft でログインしてください。",
        action="sign_in",
    )
    assert http.request is None


def test_probe_normalizes_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("relay unavailable", request=request)

    _ = _ManagedHttpMock(monkeypatch, fail)

    status = asyncio.run(probe_managed_route_status(_session_store()))

    assert status == RouteProbeStatus(
        readiness="error",
        reason_code="MANAGED_SERVICE_UNAVAILABLE",
        message="利用状況を確認できませんでした。",
        action="retry",
    )


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (
            401,
            RouteProbeStatus(
                readiness="setup_required",
                reason_code="AUTH_REQUIRED",
                message="ログインを更新してください。",
                action="sign_in",
            ),
        ),
        (
            503,
            RouteProbeStatus(
                readiness="error",
                reason_code="MANAGED_SERVICE_UNAVAILABLE",
                message="利用状況を確認できませんでした。",
                action="retry",
            ),
        ),
    ],
)
def test_probe_maps_non_success_statuses(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected: RouteProbeStatus,
) -> None:
    http = _ManagedHttpMock(monkeypatch, lambda request: httpx.Response(status_code, request=request))

    status = asyncio.run(probe_managed_route_status(_session_store()))

    assert status == expected
    assert http.response is not None and http.response.is_closed


@pytest.mark.parametrize(
    "body",
    [b"{", b"[]", b'{"managed":{}}'],
    ids=["malformed-json", "non-object", "missing-fields"],
)
def test_probe_rejects_invalid_success_payload(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    http = _ManagedHttpMock(
        monkeypatch,
        lambda request: httpx.Response(200, request=request, content=body),
    )

    status = asyncio.run(probe_managed_route_status(_session_store()))

    assert status == RouteProbeStatus(
        readiness="error",
        reason_code="INVALID_MANAGED_RESPONSE",
        message="利用状況を確認できませんでした。",
        action="retry",
    )
    assert http.response is not None and http.response.is_closed


def _entitlement_response(
    request: httpx.Request,
    *,
    readiness: str,
    reason: str,
    reply_selectable: bool = True,
) -> httpx.Response:
    return httpx.Response(
        200,
        request=request,
        json={
            "managed": {
                "readiness": readiness,
                "reason": reason,
                "reply": {"enabled": reply_selectable, "selectable": reply_selectable},
                "speech_recognition": {"enabled": True, "selectable": True},
            }
        },
    )


def test_probe_rejects_unknown_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _ManagedHttpMock(
        monkeypatch,
        lambda request: _entitlement_response(request, readiness="future_state", reason="FUTURE_STATE"),
    )

    status = asyncio.run(probe_managed_route_status(_session_store()))

    assert status == RouteProbeStatus(
        readiness="error",
        reason_code="INVALID_MANAGED_RESPONSE",
        message="利用状況を確認できませんでした。",
        action="retry",
    )


def test_probe_rejects_reply_when_only_managed_stt_is_selectable(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _ManagedHttpMock(
        monkeypatch,
        lambda request: _entitlement_response(
            request,
            readiness="ready",
            reason="READY",
            reply_selectable=False,
        ),
    )

    status = asyncio.run(probe_managed_route_status(_session_store()))

    assert status == RouteProbeStatus(
        readiness="unavailable",
        reason_code="SERVICE_DISABLED",
        message="現在、Meeting Supporter AIの返答案を利用できません。",
        action="retry",
        service_tier="standard",
    )


@pytest.mark.parametrize(
    ("managed_readiness", "reason", "expected"),
    [
        (
            "ready",
            "READY",
            RouteProbeStatus(
                readiness="ready",
                reason_code="READY",
                message="利用できます。",
                action="none",
                service_tier="standard",
            ),
        ),
        (
            "subscription_required",
            "SUBSCRIPTION_REQUIRED",
            RouteProbeStatus(
                readiness="setup_required",
                reason_code="SUBSCRIPTION_REQUIRED",
                message="月額プランの契約が必要です。",
                action="subscribe",
                service_tier="standard",
            ),
        ),
        (
            "payment_required",
            "PAYMENT_REQUIRED",
            RouteProbeStatus(
                readiness="setup_required",
                reason_code="PAYMENT_REQUIRED",
                message="支払い方法を確認してください。",
                action="manage_billing",
                service_tier="standard",
            ),
        ),
        (
            "quota_exhausted",
            "QUOTA_EXHAUSTED",
            RouteProbeStatus(
                readiness="unavailable",
                reason_code="QUOTA_EXHAUSTED",
                message="今月の共通利用枠を使い切りました。",
                action="view_usage",
                service_tier="standard",
            ),
        ),
        (
            "service_disabled",
            "SERVICE_DISABLED",
            RouteProbeStatus(
                readiness="unavailable",
                reason_code="SERVICE_DISABLED",
                message="現在この機能を利用できません。",
                action="retry",
                service_tier="standard",
            ),
        ),
        (
            "unavailable",
            "ACCOUNT_DELETING",
            RouteProbeStatus(
                readiness="unavailable",
                reason_code="ACCOUNT_DELETING",
                message="アカウントを削除しています。",
                action="none",
                service_tier="standard",
            ),
        ),
    ],
)
def test_probe_maps_entitlement_contract(
    monkeypatch: pytest.MonkeyPatch,
    managed_readiness: str,
    reason: str,
    expected: RouteProbeStatus,
) -> None:
    http = _ManagedHttpMock(
        monkeypatch,
        lambda request: _entitlement_response(request, readiness=managed_readiness, reason=reason),
    )

    status = asyncio.run(probe_managed_route_status(_session_store()))

    assert status == expected
    request = cast(httpx.Request, http.request)
    assert request.method == "GET"
    assert str(request.url) == "https://managed.example/v1/entitlement"
    assert request.headers["authorization"] == "Bearer secret-token"
    assert http.follow_redirects is False
    assert http.response is not None and http.response.is_closed
