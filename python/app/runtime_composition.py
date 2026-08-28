"""Runtime construction and atomic configuration reload coordination."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import final

from app.agents.codex_app_server import CodexAppServer
from app.agents.codex_runtime import (
    CodexInfoAgentRuntime,
    CodexMinutesAgentRuntime,
    CodexReplyAgentRuntime,
)
from app.agents.factory import AgentBundle, AgentRouteError, build_agents
from app.agents.managed_runtime import ManagedReplyAgentRuntime
from app.agents.models import ReplyAgentDefinition
from app.agents.route_catalog import CodexStatusProvider, ManagedStatusProvider, RouteCatalog
from app.audio import AudioPipeline, SoundcardSource
from app.core.config import RouteDefinition
from app.core.events import ConfigChanged
from app.core.protocols import AudioPipelineLike, SecretStore
from app.core.state import AppState
from app.services.broadcast import BroadcastManager
from app.services.config_loader import ConfigLoader
from app.services.context_loader import ensure_default_context_directory, load_context_files
from app.services.conversation_orchestrator import ConversationOrchestrator
from app.services.managed_session import ManagedSessionStore
from app.services.stt_controller import SttController
from app.services.usage_logger import UsageLogger
from app.stt import SttPipeline, build_pipeline

logger = logging.getLogger(__name__)


@final
class RuntimeCompositionCoordinator:
    """Owns runtime factories and applies effective configuration atomically."""

    def __init__(
        self,
        *,
        config: ConfigLoader,
        state: AppState,
        secret_store: SecretStore,
        broadcast_manager: BroadcastManager,
        managed_session_store: ManagedSessionStore,
        codex: CodexAppServer,
        usage_logger: UsageLogger,
        replace_ai_note: Callable[[str, str], Awaitable[str]],
        handle_speech: Callable[[str, str], Coroutine[object, object, None]],
        managed_status: ManagedStatusProvider | None,
        codex_status: CodexStatusProvider,
    ) -> None:
        self.config = config
        self._state = state
        self._secret_store = secret_store
        self._broadcast_manager = broadcast_manager
        self._managed_session_store = managed_session_store
        self._codex = codex
        self._usage_logger = usage_logger
        self._replace_ai_note = replace_ai_note
        self._handle_speech = handle_speech
        self._managed_status = managed_status
        self._codex_status = codex_status
        self._config_change_lock = asyncio.Lock()
        self.bundle = self._build_agent_bundle(config)

    async def info_route_ready(self) -> bool:
        route = await RouteCatalog(
            providers=self.config.providers,
            routes=self.config.routes,
            assignments=self.config.ai_assignments,
            secret_store=self._secret_store,
            managed_status=self._managed_status,
            codex_status=self._codex_status,
        ).read_assigned_route("info")
        return route is not None and route.readiness == "ready" and route.selectable and "info" in route.capabilities

    def make_audio(self, device: int | str | None, role: str) -> AudioPipeline:
        cfg = self.config.stt_config
        source = SoundcardSource(device, role, cfg.sample_rate)
        return AudioPipeline(source, role, self._broadcast_manager.broadcast)

    def make_stt(self, audio: AudioPipelineLike, role: str) -> SttPipeline:
        cfg = self.config.stt_config
        return build_pipeline(
            audio.stt_queue,
            role,
            cfg,
            self._broadcast_manager.broadcast,
            self._handle_speech,
            self._managed_session_store,
            lambda: self._state.current_session.id if self._state.current_session is not None else None,
        )

    async def on_config_changed(
        self,
        event: ConfigChanged,
        *,
        stt_controller: SttController,
        conversation_orchestrator: ConversationOrchestrator,
    ) -> None:
        async with self._config_change_lock:
            old_config = self.config
            new_config = old_config.reload()
            ensure_default_context_directory(
                context_dir=new_config.context_dir,
                user_data_dir=new_config.user_data_dir,
            )
            context_dir_changed = new_config.context_dir != old_config.context_dir
            composition_changed = self._agent_composition_changed(old_config, new_config)

            prepared_bundle: AgentBundle | None = None
            if composition_changed:
                try:
                    prepared_bundle = self._build_agent_bundle(new_config)
                    await self._enter_info_runtime(prepared_bundle)
                except (ValueError, RuntimeError) as error:
                    if prepared_bundle is not None:
                        await self._exit_info_runtime(prepared_bundle)
                    logger.warning("AI設定の適用に失敗したため、現在の実行構成を維持します: %s", error)
                    return

            # Publish effective config only after a replacement agent bundle is ready.
            self.config = new_config
            self._state.config = new_config
            if context_dir_changed:
                self._state.context_text = load_context_files(new_config.context_dir)
            await stt_controller.on_config_changed(
                old_config,
                new_config,
                audio_lifecycle_lock_held=event.audio_lifecycle_lock_held,
            )

            if prepared_bundle is not None:
                old_bundle = self.bundle
                self.bundle = prepared_bundle
                await conversation_orchestrator.update_agents(
                    info_runtime=self.bundle.info_runtime,
                    reply_agent_specs=self.bundle.reply_agent_specs,
                )
                await self._exit_info_runtime(old_bundle)

            await conversation_orchestrator.on_config_changed(new_config)

    def _build_agent_bundle(self, config: ConfigLoader) -> AgentBundle:
        return build_agents(
            state=self._state,
            providers=config.providers,
            routes=config.routes,
            assignments=config.ai_assignments,
            secret_store=self._secret_store,
            context_dir=config.context_dir,
            usage_logger=self._usage_logger,
            mcp_servers=config.mcp_servers,
            reply_agent_definitions=config.reply_agent_definitions,
            replace_ai_note=self._replace_ai_note,
            external_reply_factories={
                "managed": self._build_managed_reply_runtime,
                "codex": self._build_codex_reply_runtime,
            },
            external_info_factories={"codex": self._build_codex_info_runtime},
            external_minutes_factories={"codex": self._build_codex_minutes_runtime},
        )

    def _build_managed_reply_runtime(
        self,
        _route: RouteDefinition,
        definition: ReplyAgentDefinition,
    ) -> ManagedReplyAgentRuntime:
        return ManagedReplyAgentRuntime(
            session_store=self._managed_session_store,
            instruction=definition.instruction,
        )

    def _build_codex_reply_runtime(
        self,
        route: RouteDefinition,
        _definition: ReplyAgentDefinition,
    ) -> CodexReplyAgentRuntime:
        return CodexReplyAgentRuntime(peer=self._codex, model=self._required_codex_model(route))

    def _build_codex_info_runtime(self, route: RouteDefinition) -> CodexInfoAgentRuntime:
        return CodexInfoAgentRuntime(peer=self._codex, model=self._required_codex_model(route))

    def _build_codex_minutes_runtime(self, route: RouteDefinition) -> CodexMinutesAgentRuntime:
        return CodexMinutesAgentRuntime(peer=self._codex, model=self._required_codex_model(route))

    @staticmethod
    def _required_codex_model(route: RouteDefinition) -> str:
        if not route.model:
            raise AgentRouteError(
                code="CODEX_MODEL_NOT_CONFIGURED",
                message="Codex経路のモデル設定が空です。設定を確認してください。",
            )
        return route.model

    @staticmethod
    def _agent_composition_changed(
        old_config: ConfigLoader,
        new_config: ConfigLoader,
    ) -> bool:
        return (
            new_config.routes != old_config.routes
            or new_config.ai_assignments != old_config.ai_assignments
            or new_config.providers != old_config.providers
            or new_config.ollama_base_url != old_config.ollama_base_url
            or new_config.context_dir != old_config.context_dir
            or new_config.mcp_servers != old_config.mcp_servers
            or new_config.reply_agent_definitions != old_config.reply_agent_definitions
        )

    @staticmethod
    async def _enter_info_runtime(bundle: AgentBundle) -> None:
        if bundle.info_runtime is not None:
            _ = await bundle.info_runtime.__aenter__()

    @staticmethod
    async def _exit_info_runtime(bundle: AgentBundle) -> None:
        if bundle.info_runtime is not None:
            _ = await bundle.info_runtime.__aexit__(None, None, None)


__all__ = ["RuntimeCompositionCoordinator"]
