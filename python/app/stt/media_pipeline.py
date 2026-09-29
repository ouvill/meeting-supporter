"""Delivers Rust speech results to existing conversation/AI services; no PCM."""

from __future__ import annotations

import asyncio
import os
import queue
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Never, final

from app.audio.media_pipeline import MediaAudioPipeline
from app.audio.media_transport import MediaError, MediaEvent
from app.core.config import SttConfig
from app.core.messages import ErrorMsg, OutgoingBroadcastFn, SttInterimMsg, SttStateMsg
from app.core.types import HandleSpeechFn
from app.stt.reazonspeech_model import cached_reazonspeech_snapshot


@final
class MediaSttPipeline:
    def __init__(
        self,
        audio: MediaAudioPipeline,
        cfg: SttConfig,
        role: str,
        broadcast: OutgoingBroadcastFn,
        handle_speech: HandleSpeechFn,
    ) -> None:
        if cfg.backend != "reazonspeech" or cfg.sample_rate != 16000 or cfg.vad_engine != "silero":
            raise ValueError("Rust音声制御はReazonSpeech・Silero・16 kHz mono PCMを必要とします。")
        self._audio = audio
        self._cfg = cfg
        self._role = role
        self._broadcast = broadcast
        self._handle_speech = handle_speech
        self._ready = False
        self._task: asyncio.Task[None] | None = None
        self._stop_task: asyncio.Task[MediaEvent] | None = None
        self._cleanup: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._generation = 0
        self._last_end = 0
        self.on_ready: Callable[[], Coroutine[Never, Never, None]] | None = None
        self.on_error: Callable[[Exception], Coroutine[Never, Never, None]] | None = None

    def supports_prewarm(self) -> bool:
        return True

    def initialize(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._prepare())

    async def _prepare(self) -> None:
        self._ready = False
        try:
            if self._cleanup is not None:
                await self._cleanup
            model = await asyncio.to_thread(cached_reazonspeech_snapshot)
            if model is None or not Path(model).is_absolute():
                raise MediaError()
            _ = await asyncio.to_thread(
                self._audio.request,
                {
                    "op": "prepare",
                    "model": str(model),
                    "punctuation": os.environ.get("MEETING_REAZON_PUNCTUATION"),
                    "config": {
                        "vad_threshold": min(max(self._cfg.vad_sensitivity, 0.05), 0.95),
                        "silence_seconds": self._cfg.silence_duration,
                        "min_voiced_ms": self._cfg.min_voiced_ms,
                        "min_voiced_ratio": self._cfg.min_voiced_ratio,
                        "min_rms_dbfs": self._cfg.min_rms_dbfs,
                    },
                },
                135,
            )
            self._ready = True
            self._task = None
            callback, self.on_ready = self.on_ready, None
            if callback is not None:
                await callback()
        except asyncio.CancelledError:
            raise
        except Exception:
            callback, self.on_error = self.on_error, None
            self.on_ready = None
            if callback is not None:
                await callback(MediaError())
            else:
                await self._broadcast(ErrorMsg(text=str(MediaError())))

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if not self._ready or (self._task is not None and not self._task.done()):
            raise MediaError()
        self._audio.ensure_running()
        self._audio.reset_speech_events()
        self._stop_task = None
        self._started = asyncio.Event()
        self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        try:
            reply = await asyncio.to_thread(self._audio.request, {"op": "start_speech"})
            if reply.generation is None:
                raise MediaError()
            self._generation, self._last_end = reply.generation, 0
            self._started.set()
            while True:
                if self._audio.speech_failed:
                    raise MediaError()
                try:
                    event = self._audio.speech_events.get_nowait()
                except queue.Empty:
                    if self._stop_task is not None and self._stop_task.done():
                        _ = await self._stop_task
                        # The reader enqueues every transcript before its stop reply.
                        if self._audio.speech_events.empty():
                            break
                    await asyncio.sleep(0.01)
                    continue
                await self._accept(event)
        except asyncio.CancelledError:
            self._ready = False
            raise
        except Exception:
            self._ready = False
            try:
                _ = await asyncio.to_thread(self._audio.request, {"op": "shutdown_speech"}, 45)
            except MediaError:
                pass
            await self._broadcast(ErrorMsg(text=str(MediaError())))
            await self._broadcast(SttStateMsg(backend="reazonspeech", initialized=False, initializing=False))
        finally:
            self._started.set()
            await self._broadcast(SttInterimMsg(role=self._role, text=""))

    async def _accept(self, event: MediaEvent) -> None:
        if (
            event.type != "transcript"
            or event.generation != self._generation
            or event.text is None
            or event.start_sample is None
            or event.end_sample is None
            or event.end_sample < event.start_sample
            or event.end_sample <= self._last_end
        ):
            raise MediaError()
        self._last_end = event.end_sample
        if event.punctuation_failed:
            await self._broadcast(ErrorMsg(text="句読点処理に失敗したため、認識した原文を使用します。"))
        if event.text.strip():
            await self._handle_speech(self._role, event.text)

    def stop(self) -> None:
        if self._ready and self._task is not None and not self._task.done() and self._stop_task is None:
            self._stop_task = asyncio.get_running_loop().create_task(self._stop_after_start())

    async def _stop_after_start(self) -> MediaEvent:
        _ = await self._started.wait()
        if not self._ready:
            raise MediaError()
        return await asyncio.to_thread(self._audio.request, {"op": "stop_speech"}, 45)

    async def stop_and_drain(self) -> None:
        self.stop()
        if self._task is not None:
            async with asyncio.timeout(50):
                await self._task
        if self._stop_task is not None:
            _ = await self._stop_task
        if not self._ready:
            raise MediaError()

    def shutdown(self) -> None:
        self._ready = False
        self.on_ready = None
        self.on_error = None
        if self._task is not None:
            _ = self._task.cancel()
        self._cleanup = asyncio.get_running_loop().create_task(self._shutdown())

    async def _shutdown(self) -> None:
        # Worker serializes control commands; any in-flight prepare completes before
        # shutdown. This never tears down the independent recording branch.
        try:
            _ = await asyncio.to_thread(self._audio.request, {"op": "shutdown_speech"}, 45)
        except MediaError:
            pass
        if self._stop_task is not None:
            _ = await asyncio.gather(self._stop_task, return_exceptions=True)

    async def shutdown_and_wait(self) -> None:
        self.shutdown()
        if self._task is not None:
            _ = await asyncio.gather(self._task, return_exceptions=True)
        if self._cleanup is not None:
            await self._cleanup
