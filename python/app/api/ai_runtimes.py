"""Operational API for the experimental official Codex runtime."""

from __future__ import annotations

from typing import ClassVar, Literal, NoReturn
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.agents.codex_app_server import (
    MINIMUM_CODEX_VERSION_LABEL,
    CodexAppServer,
    CodexSafeError,
    ProcessState,
)
from app.agents.codex_protocol import RateLimitSnapshot, RateLimitWindow
from app.agents.route_catalog import RouteProbeStatus


class ApiModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class CodexStatusResponse(ApiModel):
    installed: bool
    version: str | None
    compatible: bool
    process_state: str
    auth_state: str
    turn_state: str
    authenticated: bool
    account_type: str | None
    plan_type: str | None
    ready: bool
    availability: Literal["experimental"] = "experimental"
    security_boundary_verified: Literal[False] = False
    reason_code: str
    message: str


class LoginResponse(ApiModel):
    login_id: str
    auth_url: str


class DeviceCodeLoginResponse(ApiModel):
    login_id: str
    verification_url: str
    user_code: str


class LoginCancelRequest(ApiModel):
    login_id: str | None = Field(default=None, min_length=1, max_length=256)


class LoginCancelResponse(ApiModel):
    status: Literal["canceled", "notFound"]


class OperationResponse(ApiModel):
    ok: bool


class RateLimitWindowResponse(ApiModel):
    used_percent: int
    resets_at: int | None
    window_duration_mins: int | None


class RateLimitBucketResponse(ApiModel):
    id: str | None
    name: str | None
    plan_type: str | None
    primary: RateLimitWindowResponse | None
    secondary: RateLimitWindowResponse | None
    has_credits: bool | None
    unlimited: bool | None
    reached_reason: str | None


class RateLimitsResponse(ApiModel):
    default: RateLimitBucketResponse
    buckets: dict[str, RateLimitBucketResponse]
    reset_credits_available: int | None


def _safe_http_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CodexSafeError(
            "invalid_auth_url",
            "Codex から安全なログイン URL を取得できませんでした。",
            retryable=False,
        )
    return value


def _raise_http(error: CodexSafeError) -> NoReturn:
    if error.code in {"not_installed", "invalid_binary", "unsupported_version", "not_logged_in", "unsupported_auth"}:
        status_code = 409
    elif error.code in {"request_timeout", "process_exited", "process_unavailable", "service_unavailable"}:
        status_code = 503
    else:
        status_code = 400
    raise HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": error.message, "retryable": error.retryable},
    ) from error


def _window(value: RateLimitWindow | None) -> RateLimitWindowResponse | None:
    if value is None:
        return None
    return RateLimitWindowResponse(
        used_percent=value.used_percent,
        resets_at=value.resets_at,
        window_duration_mins=value.window_duration_mins,
    )


def _bucket(value: RateLimitSnapshot) -> RateLimitBucketResponse:
    return RateLimitBucketResponse(
        id=value.limit_id,
        name=value.limit_name,
        plan_type=value.plan_type,
        primary=_window(value.primary),
        secondary=_window(value.secondary),
        has_credits=value.credits.has_credits if value.credits is not None else None,
        unlimited=value.credits.unlimited if value.credits is not None else None,
        reached_reason=value.rate_limit_reached_type,
    )


def _installation_message(reason_code: str, detected_version: str | None) -> str:
    detected = detected_version or "確認できません"
    version_details = f"検出バージョン: {detected}。最低対応版は {MINIMUM_CODEX_VERSION_LABEL} です。"
    restart = "インストール・更新後にアプリを再起動してください。"
    if reason_code == "not_installed":
        return (
            f"Codex CLI がインストールされていないか、見つけられません。{version_details} "
            + f"公式ページの案内に沿って Codex CLI をインストールしてください。{restart}"
        )
    if reason_code == "unsupported_version":
        return (
            f"Codex CLI を利用できる安定版として確認できません。{version_details} "
            + f"Codex CLI の安定版へ更新してください。{restart}"
        )
    if reason_code == "invalid_binary":
        return f"Codex CLI を実行できません。{version_details} " + f"Codex CLI を再インストールしてください。{restart}"
    if reason_code == "version_unavailable":
        return (
            f"Codex CLI のバージョンを確認できません。{version_details} "
            + f"Codex CLI を再インストールしてください。{restart}"
        )
    return f"Codex CLI を利用できません。{version_details} Codex CLI のインストール状態を確認してください。"


def _untested_version_message(version: str | None) -> str:
    detected = version or "不明"
    return (
        f"Codex {detected} はこのアプリでschema確認していない新版です。"
        + "起動・認証・モデル一覧の応答を確認できたため選択できます。"
        + "返答開始・stream中にもprotocolを検証し、非互換なら応答を終了してprocessを停止します。"
    )


def _auth_message(reason_code: str) -> str:
    if reason_code == "unsupported_auth":
        return "現在の認証方法では Codex を利用できません。公式 Codex CLI で ChatGPT にログインし直してください。"
    return "Codex を利用するには、公式 Codex CLI で ChatGPT にログインしてください。"


def _runtime_failure_message() -> str:
    return "Codex CLI を起動または初期化できませんでした。Codex CLI を終了してから状態を再確認してください。"


async def probe_codex_route_status(codex: CodexAppServer, requested_model: str) -> RouteProbeStatus:
    """Translate runtime and configured-model health without exposing the unverified boundary."""

    snapshot = await codex.status()
    installation = snapshot.installation
    if not installation.compatible:
        reason = installation.reason_code or "not_available"
        return RouteProbeStatus(
            readiness="unavailable",
            reason_code=reason,
            message=_installation_message(reason, installation.version),
            action="install",
        )
    if snapshot.process_state is ProcessState.FAILED:
        return RouteProbeStatus(
            readiness="error",
            reason_code="runtime_unavailable",
            message=_runtime_failure_message(),
            action="start",
        )
    account = snapshot.account
    if account is None or not account.authenticated:
        reason = "not_logged_in" if account is None or account.account_type is None else "unsupported_auth"
        return RouteProbeStatus(
            readiness="setup_required",
            reason_code=reason,
            message=_auth_message(reason),
            action="login",
        )
    try:
        service_tier_status = await codex.model_service_tier_status(requested_model, effort="low")
    except CodexSafeError as error:
        if error.code in {"not_logged_in", "unsupported_auth"}:
            return RouteProbeStatus(
                readiness="setup_required",
                reason_code=error.code,
                message=_auth_message(error.code),
                action="login",
            )
        if error.code in {"model_not_configured", "model_unavailable", "reasoning_effort_unavailable"}:
            return RouteProbeStatus(
                readiness="setup_required",
                reason_code="CODEX_MODEL_UNAVAILABLE",
                message=(
                    "選択されている Codex モデルは低い推論強度で利用できません。"
                    + "モデルまたは ChatGPT の契約を確認してください。"
                ),
                action="configure",
            )
        if error.code in {
            "process_exited",
            "process_unavailable",
            "runtime_closed",
            "service_unavailable",
            "request_timeout",
        }:
            return RouteProbeStatus(
                readiness="error",
                reason_code="runtime_unavailable",
                message=_runtime_failure_message(),
                action="start",
            )
        return RouteProbeStatus(
            readiness="error",
            reason_code="CODEX_MODEL_LIST_CHECK_FAILED",
            message="Codex で利用可能なモデルを確認できませんでした。状態を再確認してください。",
            action="none",
        )
    service_tier = service_tier_status.effective or service_tier_status.advertised
    priority_fallback = service_tier_status.advertised == "priority" and service_tier == "standard"
    return RouteProbeStatus(
        readiness="ready",
        reason_code=("local_file_isolation_unverified" if installation.schema_verified else "untested_newer_version"),
        message=(
            _untested_version_message(installation.version)
            if not installation.schema_verified
            else "標準速度（速度優先を利用できないため）"
            if priority_fallback
            else "速度優先（通常より利用量が増えます）"
            if service_tier == "priority"
            else "標準速度"
        ),
        action="none",
        service_tier=service_tier,
    )


def create_router(*, codex: CodexAppServer) -> APIRouter:
    """Create runtime operations without owning application lifecycle wiring."""

    router = APIRouter(prefix="/api/ai-runtimes/codex", tags=["ai-runtimes"])

    @router.get("/status")
    async def status() -> CodexStatusResponse:  # pyright: ignore[reportUnusedFunction]
        snapshot = await codex.status()
        account = snapshot.account
        technically_ready = (
            snapshot.installation.compatible
            and snapshot.process_state is ProcessState.READY
            and account is not None
            and account.authenticated
        )
        if not snapshot.installation.compatible:
            reason_code = snapshot.installation.reason_code or "not_available"
            message = _installation_message(reason_code, snapshot.installation.version)
        elif snapshot.process_state is ProcessState.FAILED:
            reason_code = "runtime_unavailable"
            message = _runtime_failure_message()
        elif account is None or not account.authenticated:
            reason_code = "not_logged_in" if account is None or account.account_type is None else "unsupported_auth"
            message = _auth_message(reason_code)
        elif not snapshot.installation.schema_verified:
            reason_code = "untested_newer_version"
            message = _untested_version_message(snapshot.installation.version)
        else:
            reason_code = "local_file_isolation_unverified"
            message = (
                "実験的機能です。ローカルファイル読取の分離境界は未検証のため、"
                "機密ファイルを含む環境では使用しないでください。"
            )
        return CodexStatusResponse(
            installed=snapshot.installation.binary is not None,
            version=snapshot.installation.version,
            compatible=snapshot.installation.compatible,
            process_state=snapshot.process_state.value,
            auth_state=snapshot.auth_state.value,
            turn_state=snapshot.turn_state.value,
            authenticated=account.authenticated if account is not None else False,
            account_type=account.account_type if account is not None else None,
            plan_type=account.plan_type if account is not None else None,
            ready=technically_ready,
            reason_code=reason_code,
            message=message,
        )

    @router.post("/login")
    async def start_login() -> LoginResponse:  # pyright: ignore[reportUnusedFunction]
        try:
            result = await codex.start_login()
            return LoginResponse(login_id=result.login_id, auth_url=_safe_http_url(result.auth_url))
        except CodexSafeError as error:
            _raise_http(error)

    @router.post("/login/device-code")
    async def start_device_code_login() -> DeviceCodeLoginResponse:  # pyright: ignore[reportUnusedFunction]
        try:
            result = await codex.start_device_code_login()
            return DeviceCodeLoginResponse(
                login_id=result.login_id,
                verification_url=_safe_http_url(result.verification_url),
                user_code=result.user_code.get_secret_value(),
            )
        except CodexSafeError as error:
            _raise_http(error)

    @router.post("/login/cancel")
    async def cancel_login(  # pyright: ignore[reportUnusedFunction]
        body: LoginCancelRequest | None = None,
    ) -> LoginCancelResponse:
        try:
            result = await codex.cancel_login(body.login_id if body is not None else None)
            return LoginCancelResponse(status=result.status)
        except CodexSafeError as error:
            _raise_http(error)

    @router.post("/logout")
    async def logout() -> OperationResponse:  # pyright: ignore[reportUnusedFunction]
        try:
            await codex.logout()
            return OperationResponse(ok=True)
        except CodexSafeError as error:
            _raise_http(error)

    @router.get("/rate-limits")
    async def rate_limits() -> RateLimitsResponse:  # pyright: ignore[reportUnusedFunction]
        try:
            result = await codex.read_rate_limits()
            return RateLimitsResponse(
                default=_bucket(result.rate_limits),
                buckets={key: _bucket(value) for key, value in (result.rate_limits_by_limit_id or {}).items()},
                reset_credits_available=(
                    result.rate_limit_reset_credits.available_count
                    if result.rate_limit_reset_credits is not None
                    else None
                ),
            )
        except CodexSafeError as error:
            _raise_http(error)

    @router.post("/cancel")
    async def cancel_active_turn() -> OperationResponse:  # pyright: ignore[reportUnusedFunction]
        try:
            canceled = await codex.interrupt_active_turn()
            return OperationResponse(ok=canceled)
        except CodexSafeError as error:
            _raise_http(error)

    return router


__all__ = [
    "CodexStatusResponse",
    "DeviceCodeLoginResponse",
    "LoginCancelRequest",
    "LoginCancelResponse",
    "LoginResponse",
    "OperationResponse",
    "RateLimitsResponse",
    "probe_codex_route_status",
    "create_router",
]
