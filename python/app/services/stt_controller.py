import asyncio
import logging
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

logger = logging.getLogger(__name__)

from app.core.messages import (
    DevicesListMsg,
    ErrorMsg,
    MeetingStateMsg,
    OutgoingBroadcastFn,
    StatusMsg,
    SttStateMsg,
)
from app.core.protocols import AudioPipelineLike, SttState, SttStreamLike, WebSocketLike
from app.core.types import InputDevice

if TYPE_CHECKING:
    from app.services.config_loader import ConfigLoader


def _format_timeout_duration(seconds: float) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        return f"{int(seconds // 60)}分"
    return f"{seconds:g}秒"


class SttController:
    """WebSocket command layer for audio and STT session lifecycle.

    AudioPipeline (audio capture + volume) runs from the first device selection.
    SttPipeline (VAD + STT) runs only during meetings.

    Layers:
        AudioPipeline  — always-on while device selected; replaced on device change
        SttPipeline    — attached to AudioPipeline.stt_queue; only active during meeting
    """

    def __init__(
        self,
        state: SttState,
        backend: str,
        make_audio: Callable[[int | str | None, str], AudioPipelineLike],
        make_stt: Callable[[AudioPipelineLike, str], SttStreamLike],
        get_input_devices: Callable[[], list[InputDevice]],
        broadcast: OutgoingBroadcastFn,
        init_timeout_seconds: float | None = None,
    ) -> None:
        self._state: SttState = state
        self._backend: str = backend
        self._make_audio: Callable[[int | str | None, str], AudioPipelineLike] = make_audio
        self._make_stt: Callable[[AudioPipelineLike, str], SttStreamLike] = make_stt
        self._get_input_devices: Callable[[], list[InputDevice]] = get_input_devices
        self._broadcast: OutgoingBroadcastFn = broadcast
        self._init_timeout_seconds: float | None = init_timeout_seconds
        self._init_generation: int = 0

        self._audio_other: AudioPipelineLike | None = None
        self._audio_self: AudioPipelineLike | None = None

    # ── Audio pipelines (level monitors) ─────────────────────────────────────

    @property
    def audio_other(self) -> AudioPipelineLike | None:
        """The "other" audio pipeline (loopback / external mic)."""
        return self._audio_other

    @property
    def audio_self(self) -> AudioPipelineLike | None:
        """The "self" audio pipeline (user mic)."""
        return self._audio_self

    async def start_level_monitors(self) -> None:
        """Create and start AudioPipeline instances for both devices.

        Safe to call multiple times — existing pipelines are stopped first.
        Volume monitoring begins immediately; STT is not started here.
        """
        loop = asyncio.get_event_loop()
        self.stop_level_monitors()

        for device, role, attr in (
            (self._state.device_other, "other", "_audio_other"),
            (self._state.device_self, "self", "_audio_self"),
        ):
            try:
                pipeline: AudioPipelineLike = self._make_audio(device, role)
                pipeline.start(loop)
                setattr(self, attr, pipeline)
            except Exception as e:
                logger.warning("AudioPipeline 作成失敗 (%s): %s", role, e)

    def stop_level_monitors(self) -> None:
        """Stop both audio pipelines synchronously."""
        for attr in ("_audio_other", "_audio_self"):
            pipeline: AudioPipelineLike | None = cast(AudioPipelineLike | None, getattr(self, attr))
            if pipeline is not None:
                try:
                    pipeline.stop()
                except Exception as e:
                    logger.warning("AudioPipeline 停止失敗: %s", e)
            setattr(self, attr, None)

    # ── Config hot-reload ─────────────────────────────────────────────────────

    async def on_config_changed(self, old: "ConfigLoader", new: "ConfigLoader") -> None:
        """Apply STT config changes at runtime via pipeline hot-swap where possible."""
        if new.stt_backend == old.stt_backend and new.stt_config == old.stt_config:
            return

        if self._state.stt_other is not None and self._state.stt_self is not None:
            self._state.stt_other.apply_config(new.stt_config)
            self._state.stt_self.apply_config(new.stt_config)
            self._backend = new.stt_backend
            return

        await self.reinitialize(
            backend=new.stt_backend,
            make_audio=self._make_audio,
            make_stt=self._make_stt,
        )

    async def reinitialize(
        self,
        *,
        backend: str,
        make_audio: Callable[[int | str | None, str], AudioPipelineLike],
        make_stt: Callable[[AudioPipelineLike, str], SttStreamLike],
    ) -> None:
        """Swap factories and restart audio + STT pipelines."""
        was_running = self._state.is_running
        if was_running:
            await self.stop_meeting()
        await self.shutdown_stt()
        self.stop_level_monitors()
        self._backend = backend
        self._make_audio = make_audio
        self._make_stt = make_stt
        await self.start_level_monitors()
        if was_running:
            await self._broadcast(StatusMsg(text="音声認識の設定を変更しました。会議を再開してください"))

    # ── Device management ─────────────────────────────────────────────────────

    async def broadcast_devices(self) -> None:
        await self._broadcast(
            DevicesListMsg(
                devices=self._get_input_devices(),
                current_other=self._state.device_other,
                current_self=self._state.device_self,
            )
        )

    async def set_device(self, role: str, raw: int | str | None) -> None:
        if raw is None:
            device: int | str | None = None
        else:
            try:
                device = int(raw)
            except (ValueError, TypeError):
                device = str(raw)

        if role == "other":
            self._state.device_other = device
        elif role == "self":
            self._state.device_self = device

        await self.broadcast_devices()
        await self.start_level_monitors()

    # ── STT prewarm ───────────────────────────────────────────────────────────

    async def init_stt(self) -> None:
        if self._state.stt_initializing or self._state.stt_initialized:
            return
        if self._audio_other is None:
            await self._broadcast(ErrorMsg(text="先にデバイスを選択してください"))
            return

        self._init_generation += 1
        init_generation = self._init_generation

        await self._broadcast(StatusMsg(text="音声入力を確認しています..."))
        try:
            candidate = self._make_stt(self._audio_other, "other")
        except Exception as e:
            logger.error("STT init stream作成失敗(other): %s", e)
            traceback.print_exc()
            self._state.stt_initializing = False
            self._state.stt_initialized = False
            await self._broadcast(ErrorMsg(text=f"音声認識の準備に失敗しました: {e}"))
            return
        if not candidate.supports_prewarm():
            return
        if init_generation != self._init_generation:
            try:
                candidate.shutdown()
            except Exception as e:
                logger.warning("Cancelled STT init stream shutdown failed: %s", e)
            return

        self._state.stt_initializing = True
        await self._broadcast(SttStateMsg(backend=self._backend, initialized=False, initializing=True))
        await self._broadcast(StatusMsg(text="音声認識モデルを読み込む準備をしています..."))

        loop = asyncio.get_event_loop()
        ready_count = 0

        init_failed = False
        timeout_task: asyncio.Task[None] | None = None

        def is_current_init() -> bool:
            return init_generation == self._init_generation

        def cancel_timeout() -> None:
            current_task = asyncio.current_task()
            if timeout_task is not None and timeout_task is not current_task:
                _ = timeout_task.cancel()

        def shutdown_initializing_streams() -> None:
            for stream in (self._state.stt_other, self._state.stt_self):
                if stream is None:
                    continue
                try:
                    stream.shutdown()
                except Exception as e:
                    logger.warning("STT init timeout shutdown failed: %s", e)
            self._state.stt_other = None
            self._state.stt_self = None

        async def fail_initialization(message: str) -> None:
            nonlocal init_failed
            if init_failed or not is_current_init():
                return
            init_failed = True
            cancel_timeout()
            shutdown_initializing_streams()
            self._state.stt_initialized = False
            self._state.stt_initializing = False
            await self._broadcast(ErrorMsg(text=message))
            await self._broadcast(SttStateMsg(backend=self._backend, initialized=False, initializing=False))

        async def on_init_timeout(timeout_seconds: float) -> None:
            await asyncio.sleep(timeout_seconds)
            timeout = _format_timeout_duration(timeout_seconds)
            message = (
                f"音声認識の準備が{timeout}以内に完了しませんでした。"
                + "デバイスとモデル設定を確認して再試行してください"
            )
            await fail_initialization(message)

        timeout_task = (
            loop.create_task(on_init_timeout(self._init_timeout_seconds))
            if self._init_timeout_seconds is not None
            else None
        )

        async def on_stream_ready() -> None:
            nonlocal ready_count
            if init_failed or not is_current_init():
                return
            ready_count += 1
            if ready_count >= 2:
                cancel_timeout()
                self._state.stt_initialized = True
                self._state.stt_initializing = False
                await self._broadcast(StatusMsg(text="音声認識の準備ができました。"))
                await self._broadcast(SttStateMsg(backend=self._backend, initialized=True, initializing=False))

        async def on_stream_error(exc: Exception) -> None:
            if not is_current_init():
                return
            await fail_initialization(f"音声認識の準備に失敗しました: {exc}")

        self._state.stt_other = candidate
        try:
            if self._audio_self is None:
                raise RuntimeError("self デバイスの AudioPipeline がありません")
            self._state.stt_self = self._make_stt(self._audio_self, "self")
        except Exception as e:
            logger.error("STT init stream作成失敗(self): %s", e)
            traceback.print_exc()
            await fail_initialization(f"音声認識の準備に失敗しました: {e}")
            return
        if not is_current_init():
            shutdown_initializing_streams()
            return

        stt_other: SttStreamLike | None = cast(SttStreamLike | None, self._state.stt_other)
        stt_self: SttStreamLike | None = cast(SttStreamLike | None, self._state.stt_self)
        if stt_other is None or stt_self is None:
            raise RuntimeError("STT stream が初期化されていません")
        stt_other.on_ready = on_stream_ready
        stt_other.on_error = on_stream_error
        stt_self.on_ready = on_stream_ready
        stt_self.on_error = on_stream_error
        await self._broadcast(StatusMsg(text="音声認識モデルを読み込んでいます..."))
        stt_other.initialize(loop)
        stt_self.initialize(loop)

    async def shutdown_stt(self) -> None:
        self._init_generation += 1
        if not self._state.stt_other or not self._state.stt_other.supports_prewarm():
            if self._state.stt_initializing or self._state.stt_initialized:
                self._state.stt_initialized = False
                self._state.stt_initializing = False
                await self._broadcast(SttStateMsg(backend=self._backend, initialized=False, initializing=False))
            return

        if self._state.is_running:
            await self._broadcast(StatusMsg(text="待機中"))
            await self._broadcast(MeetingStateMsg(running=False))

        try:
            self._state.stt_other.shutdown()
        except Exception as e:
            logger.error("STT shutdown(other) 失敗: %s", e)
            traceback.print_exc()
            await self._broadcast(ErrorMsg(text=f"音声認識の停止に失敗しました: {e}"))
        finally:
            self._state.stt_other = None

        if self._state.stt_self:
            try:
                self._state.stt_self.shutdown()
            except Exception as e:
                logger.error("STT shutdown(self) 失敗: %s", e)
                traceback.print_exc()
                await self._broadcast(ErrorMsg(text=f"音声認識の停止に失敗しました: {e}"))
            finally:
                self._state.stt_self = None

        self._state.stt_initialized = False
        self._state.stt_initializing = False
        await self._broadcast(SttStateMsg(backend=self._backend, initialized=False, initializing=False))

    # ── Meeting lifecycle ─────────────────────────────────────────────────────

    async def start_meeting(self, ws: WebSocketLike, *, session_already_started: bool = False) -> bool:
        """Start STT streams and broadcast meeting state.

        Returns ``True`` on success, ``False`` on early-exit failure paths.
        When *session_already_started* is ``True`` the ``is_running`` guard is
        skipped — the caller (e.g. ``MeetingLifecycleCoordinator``) has already
        set the session in ``AppState`` per ADR-003.
        """
        if not session_already_started and self._state.is_running:
            await ws.send_json(StatusMsg(text="すでに会議中です").model_dump())
            return False
        if self._audio_other is None:
            await ws.send_json(ErrorMsg(text="先にデバイスを選択してください").model_dump())
            return False

        loop = asyncio.get_event_loop()
        if self._state.stt_other is None:
            try:
                self._audio_other.flush_stt_queue()
                self._state.stt_other = self._make_stt(self._audio_other, "other")
                if self._audio_self is not None:
                    self._audio_self.flush_stt_queue()
                    self._state.stt_self = self._make_stt(self._audio_self, "self")
            except Exception as e:
                logger.error("STT stream作成失敗(start_meeting): %s", e)
                traceback.print_exc()
                self._state.stt_other = None
                self._state.stt_self = None
                await ws.send_json(ErrorMsg(text=f"音声認識の開始準備に失敗しました: {e}").model_dump())
                await self._broadcast(ErrorMsg(text=f"音声認識の開始準備に失敗しました: {e}"))
                return False

        stt_other: SttStreamLike | None = cast(SttStreamLike | None, self._state.stt_other)
        if stt_other is None:
            raise RuntimeError("STT stream (other) が初期化されていません")
        if stt_other.supports_prewarm() and not self._state.stt_initialized:
            await ws.send_json(ErrorMsg(text="先に音声認識を準備してください").model_dump())
            return False

        try:
            stt_other.start(loop)
            if self._state.stt_self:
                self._state.stt_self.start(loop)
        except Exception as e:
            logger.error("STT start 失敗: %s", e)
            traceback.print_exc()
            await self._broadcast(ErrorMsg(text=f"音声認識の開始に失敗しました: {e}"))
            return False

        await self._broadcast(StatusMsg(text="会議中"))
        await self._broadcast(MeetingStateMsg(running=True))
        return True

    async def stop_meeting(self) -> None:
        for attr, label in (("stt_other", "other"), ("stt_self", "self")):
            stt: SttStreamLike | None = cast(SttStreamLike | None, getattr(self._state, attr))
            if stt is None:
                continue
            try:
                stt.stop()
            except Exception as e:
                logger.error("STT stop(%s) 失敗: %s", label, e)
                traceback.print_exc()
                await self._broadcast(ErrorMsg(text=f"音声認識の停止に失敗しました: {e}"))
            if not stt.supports_prewarm():
                setattr(self._state, attr, None)

        await self._broadcast(StatusMsg(text="待機中"))
        await self._broadcast(MeetingStateMsg(running=False))
