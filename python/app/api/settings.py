"""Settings endpoints: GET/POST /api/settings."""

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, status
from fastapi.exceptions import RequestValidationError

from app.agents.route_catalog import (
    CodexStatusProvider,
    ManagedStatusProvider,
    OllamaStatusProvider,
    RouteCatalog,
    RouteCatalogResponse,
    route_supports,
)
from app.api.settings_models import (
    ConnectionProvider,
    ConnectionTestRequest,
    ConnectionTestResponse,
    OllamaModelsResponse,
    RouteAssignmentsUpdate,
    SaveSettingsResponse,
    SettingsConflictResponse,
    SettingsResponse,
    SettingsSaveRequest,
)
from app.core.config import AiRouteAssignments, RouteCapability
from app.core.event_bus import EventBus
from app.core.events import ConfigChanged
from app.core.protocols import TransactionalSecretStore
from app.services.settings_service import (
    AudioSettingsLockedError,
    SettingsPatchError,
    SettingsValidationError,
    build_settings_response_data,
    write_ai_assignments,
)
from app.services.settings_service import (
    save_settings as persist_settings,
)
from app.services.settings_store import SettingsStore

if TYPE_CHECKING:
    from app.core.state import AppState

_CONNECTION_ENDPOINTS: dict[ConnectionProvider, tuple[str, str, dict[str, str]]] = {
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1/models", {"Authorization": "Bearer {api_key}"}),
    "deepgram": ("DEEPGRAM_API_KEY", "https://api.deepgram.com/v1/projects", {"Authorization": "Token {api_key}"}),
    "xai": ("XAI_API_KEY", "https://api.x.ai/v1/models", {"Authorization": "Bearer {api_key}"}),
    "gemini": (
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/models",
        {"x-goog-api-key": "{api_key}"},
    ),
    "anthropic": (
        "ANTHROPIC_API_KEY",
        "https://api.anthropic.com/v1/models",
        {"x-api-key": "{api_key}", "anthropic-version": "2023-06-01"},
    ),
}


def _connection_test_response(
    provider: ConnectionProvider,
    api_key: str | None,
) -> ConnectionTestResponse:
    """Verify a provider credential with a side-effect-free models request."""
    _secret_key, url, header_templates = _CONNECTION_ENDPOINTS[provider]
    if not api_key:
        return ConnectionTestResponse(ok=False, status="invalid", message="APIキーが設定されていません。")

    headers = {"Accept": "application/json"}
    headers.update({name: value.format(api_key=api_key) for name, value in header_templates.items()})
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # pyright: ignore[reportAny]
            status_code: int = response.status  # pyright: ignore[reportAny]
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return ConnectionTestResponse(ok=False, status="invalid", message="APIキーを確認してください。")
        return ConnectionTestResponse(ok=False, status="unavailable", message="サービスに接続できませんでした。")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return ConnectionTestResponse(ok=False, status="unavailable", message="サービスに接続できませんでした。")

    if 200 <= status_code < 300:
        return ConnectionTestResponse(ok=True, status="verified", message="接続を確認しました。")
    if status_code in (401, 403):
        return ConnectionTestResponse(ok=False, status="invalid", message="APIキーを確認してください。")
    return ConnectionTestResponse(ok=False, status="unavailable", message="サービスに接続できませんでした。")


def _route_catalog(
    *,
    state: "AppState",
    assignments: AiRouteAssignments | None = None,
    managed_status: ManagedStatusProvider | None = None,
    codex_status: CodexStatusProvider | None = None,
    ollama_status: OllamaStatusProvider | None = None,
) -> RouteCatalog:
    return RouteCatalog(
        providers=state.config.providers,
        routes=state.config.routes,
        assignments=assignments or state.config.ai_assignments,
        secret_store=state.secret_store,
        managed_status=managed_status,
        codex_status=codex_status,
        ollama_status=ollama_status,
    )


def _route_assignment_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": message, "retryable": False},
    )


# ── Router factory ─────────────────────────────────────────────────────────────


def create_router(
    *,
    state: "AppState",
    store: SettingsStore,
    event_bus: EventBus,
    managed_status: ManagedStatusProvider | None = None,
    codex_status: CodexStatusProvider | None = None,
    ollama_status: OllamaStatusProvider | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    secret_store = state.secret_store
    if not isinstance(secret_store, TransactionalSecretStore):
        raise TypeError("settings router requires a transactional secret store with snapshot and restore support")
    transactional_secret_store: TransactionalSecretStore = secret_store

    @router.get("/ai/routes")
    async def get_ai_routes() -> RouteCatalogResponse:  # pyright: ignore[reportUnusedFunction]
        return await _route_catalog(
            state=state,
            managed_status=managed_status,
            codex_status=codex_status,
            ollama_status=ollama_status,
        ).read()

    @router.put("/ai/routes/assignments")
    async def replace_ai_route_assignments(  # pyright: ignore[reportUnusedFunction]
        body: RouteAssignmentsUpdate,
    ) -> RouteCatalogResponse:
        candidate = AiRouteAssignments(reply=body.reply, info=body.info, minutes=body.minutes)
        current = await _route_catalog(
            state=state,
            managed_status=managed_status,
            assignments=candidate,
            codex_status=codex_status,
            ollama_status=ollama_status,
        ).read()
        by_id = {route.id: route for route in current.routes}
        assignments_by_use_case: tuple[tuple[RouteCapability, str | None], ...] = (
            ("reply", candidate.reply),
            ("info", candidate.info),
            ("minutes", candidate.minutes),
        )
        for use_case, route_id in assignments_by_use_case:
            if route_id is None:
                continue
            route = by_id.get(route_id)
            if route is None:
                raise _route_assignment_error("AI_ROUTE_NOT_FOUND", "指定されたAI経路は存在しません。")
            if not route_supports(route, use_case):
                raise _route_assignment_error(
                    "AI_ROUTE_NOT_SELECTABLE",
                    f"指定されたAI経路は{use_case}に選択できません。",
                )
        write_ai_assignments(store, candidate)
        await event_bus.publish(ConfigChanged())
        return current

    @router.get("/settings")
    async def get_settings() -> SettingsResponse:  # pyright: ignore[reportUnusedFunction]
        return SettingsResponse.model_validate(build_settings_response_data(state=state, store=store))

    def locked_audio_settings_error() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "AUDIO_SETTINGS_LOCKED",
                "message": "会議中は音声認識の設定を変更できません。会議を終了してから変更してください。",
            },
        )

    @router.post(
        "/settings",
        response_model=SaveSettingsResponse,
        responses={status.HTTP_409_CONFLICT: {"model": SettingsConflictResponse}},
    )
    async def save_settings(  # pyright: ignore[reportUnusedFunction]
        body: SettingsSaveRequest,
    ) -> SaveSettingsResponse:
        try:
            result = await persist_settings(
                body=body.model_dump_toml(),
                state=state,
                store=store,
                event_bus=event_bus,
                secret_store=transactional_secret_store,
            )
        except SettingsPatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SettingsValidationError as exc:
            raise RequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "audio"),
                        "msg": str(exc),
                    }
                ]
            ) from exc
        except AudioSettingsLockedError as exc:
            raise locked_audio_settings_error() from exc

        settings = SettingsResponse.model_validate(
            build_settings_response_data(
                state=state,
                store=store,
                merged_agent_settings=result.merged_agent_settings,
                ollama_base_url_override=result.ollama_base_url_override,
                reply_style_tables=result.reply_style_tables,
            )
        )
        return SaveSettingsResponse(ok=True, settings=settings)

    @router.post("/settings/connections/test", response_model=ConnectionTestResponse)
    def test_connection(  # pyright: ignore[reportUnusedFunction]
        body: ConnectionTestRequest,
    ) -> ConnectionTestResponse:
        """Test an unsaved draft key, or a configured credential, without persisting it."""
        secret_key, _, _ = _CONNECTION_ENDPOINTS[body.provider]
        api_key = body.api_key if body.api_key else state.secret_store.get(secret_key)
        return _connection_test_response(body.provider, api_key)

    @router.get("/settings/ollama/models")
    def get_ollama_models(  # pyright: ignore[reportUnusedFunction]
        base_url: str | None = None,
    ) -> OllamaModelsResponse:
        """Fetch available models from an Ollama server.

        Calls ``GET {base_url}/models`` which for the default base_url
        ``http://localhost:11434/v1`` becomes ``http://localhost:11434/v1/models``.

        Returns a typed response with ok: bool, base_url, models: list[str], message: str | None.
        On connection failure, returns 200 with ok=false and a user-friendly Japanese message.

        Query parameter ``base_url`` overrides the configured Ollama base URL.
        """
        effective_base_url = base_url if base_url else state.config.ollama_base_url
        # Strip trailing slash for consistent URL construction
        effective_base_url = effective_base_url.rstrip("/")
        models_url = f"{effective_base_url}/models"

        raw_body: str
        try:
            req = urllib.request.Request(models_url, method="GET")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=5) as response:  # pyright: ignore[reportAny]
                status_code: int = response.status  # pyright: ignore[reportAny]
                if status_code != 200:
                    return OllamaModelsResponse(
                        ok=False,
                        base_url=effective_base_url,
                        models=[],
                        message=f"Ollamaサーバーからエラー応答がありました (HTTP {status_code})",
                    )
                raw_body = response.read().decode("utf-8")  # pyright: ignore[reportAny]
        except urllib.error.HTTPError as e:
            return OllamaModelsResponse(
                ok=False,
                base_url=effective_base_url,
                models=[],
                message=f"Ollamaサーバーからエラー応答がありました (HTTP {e.code})",
            )
        except urllib.error.URLError as e:
            # urllib wraps TimeoutError in URLError, so check e.reason for it
            if isinstance(e.reason, TimeoutError):
                return OllamaModelsResponse(
                    ok=False,
                    base_url=effective_base_url,
                    models=[],
                    message="Ollamaサーバーへの接続がタイムアウトしました",
                )
            return OllamaModelsResponse(
                ok=False,
                base_url=effective_base_url,
                models=[],
                message="Ollamaサーバーに接続できませんでした",
            )
        except (OSError, ValueError):
            return OllamaModelsResponse(
                ok=False,
                base_url=effective_base_url,
                models=[],
                message="Ollamaサーバーとの通信に失敗しました",
            )

        # Parse OpenAI-compatible response: {"data": [{"id": "..."}, ...]}
        try:
            parsed_raw: object = json.loads(raw_body)  # pyright: ignore[reportAny]
        except json.JSONDecodeError:
            return OllamaModelsResponse(
                ok=False,
                base_url=effective_base_url,
                models=[],
                message="Ollamaサーバーからの応答を解析できませんでした",
            )

        if not isinstance(parsed_raw, dict):
            return OllamaModelsResponse(
                ok=False,
                base_url=effective_base_url,
                models=[],
                message="Ollamaサーバーからの応答形式が不正です",
            )

        # Cast to dict[str, object] after isinstance check
        parsed: dict[str, object] = parsed_raw  # pyright: ignore[reportUnknownVariableType]
        data: object = parsed.get("data")
        if not isinstance(data, list):
            return OllamaModelsResponse(
                ok=False,
                base_url=effective_base_url,
                models=[],
                message="Ollamaサーバーからの応答にモデル一覧が含まれていません",
            )

        model_ids: list[str] = []
        for entry in data:  # pyright: ignore[reportUnknownVariableType]
            if isinstance(entry, dict):
                entry_dict: dict[str, object] = entry  # pyright: ignore[reportUnknownVariableType]
                model_id: object = entry_dict.get("id")
                if isinstance(model_id, str) and model_id:
                    model_ids.append(model_id)

        return OllamaModelsResponse(
            ok=True,
            base_url=effective_base_url,
            models=model_ids,
            message=None,
        )

    return router


__all__ = ["create_router"]
