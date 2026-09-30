"""Runtime construction and atomic configuration reload coordination."""

import asyncio
import logging
import os
from collections.abc import Callable, Coroutine
from typing import final

from app.agents.factory import AgentBundle, build_agents
from app.agents.managed_runtime import ManagedReplyAgentRuntime
from app.agents.models import ReplyAgentDefinition
from app.audio import AudioPipeline, SoundcardSource
from app.audio.media_pipeline import MediaAudioPipeline
from app.audio.media_transport import enabled as native_media_enabled
from app.audio.native_pipeline import NativeAudioPipeline
from app.audio.native_transport import enabled as native_audio_enabled
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
from app.stt import build_pipeline
from app.stt.media_pipeline import MediaSttPipeline
from app.stt.native_pipeline import NativeSttPipeline
from app.stt.pipeline import SttPipeline

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
        usage_logger: UsageLogger,
        handle_speech: Callable[[str, str], Coroutine[object, object, None]],
    ) -> None:
        self.config = config
        self._state = state
        self._secret_store = secret_store
        self._broadcast_manager = broadcast_manager
        self._managed_session_store = managed_session_store
        self._usage_logger = usage_logger
        self._handle_speech = handle_speech
        self._config_change_lock = asyncio.Lock()
        self.bundle = self._build_agent_bundle(config)

    def make_audio(
        self, device: int | str | None, role: str
    ) -> AudioPipeline | NativeAudioPipeline | MediaAudioPipeline:
        cfg = self.config.stt_config
        if native_audio_enabled() or native_media_enabled():
            if (
                cfg.backend != "reazonspeech"
                or os.environ.get("MEETING_REAZON_RUNTIME") != "rust"
                or cfg.sample_rate != 16000
            ):
                raise ValueError("Rust音声取得は現在、Rust ReazonSpeechと16 kHz入力に対応しています。")
            if native_media_enabled():
                return MediaAudioPipeline(device, role, self._broadcast_manager.broadcast)
            return NativeAudioPipeline(device, role, self._broadcast_manager.broadcast)
        source = SoundcardSource(device, role, cfg.sample_rate)
        return AudioPipeline(source, role, self._broadcast_manager.broadcast)

    def make_stt(self, audio: AudioPipelineLike, role: str) -> SttPipeline | NativeSttPipeline | MediaSttPipeline:
        cfg = self.config.stt_config
        if isinstance(audio, MediaAudioPipeline):
            return MediaSttPipeline(audio, cfg, role, self._broadcast_manager.broadcast, self._handle_speech)
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
                except (ValueError, RuntimeError) as error:
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
                self.bundle = prepared_bundle
                await conversation_orchestrator.update_agents(
                    reply_agent_specs=self.bundle.reply_agent_specs,
                )

            await conversation_orchestrator.on_config_changed(new_config)

    def _build_agent_bundle(self, config: ConfigLoader) -> AgentBundle:
        return build_agents(
            state=self._state,
            providers=config.providers,
            routes=config.routes,
            assignments=config.ai_assignments,
            secret_store=self._secret_store,
            usage_logger=self._usage_logger,
            reply_agent_definitions=config.reply_agent_definitions,
            external_reply_factories={
                "managed": self._build_managed_reply_runtime,
            },
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
            or new_config.reply_agent_definitions != old_config.reply_agent_definitions
        )


__all__ = ["RuntimeCompositionCoordinator"]
