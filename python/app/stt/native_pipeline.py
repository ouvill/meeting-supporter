"""Temporary bridge: existing capture/meeting ownership, Rust speech inference.

Select with MEETING_REAZON_RUNTIME=rust. No native code is loaded in Python.
Each input role owns a worker, so cancellation cannot affect another source.
"""

from __future__ import annotations

import asyncio
import os
import queue
import struct
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Never, final

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import ErrorMsg, OutgoingBroadcastFn, SttInterimMsg, SttStateMsg
from app.core.types import HandleSpeechFn
from app.stt.native_worker import NativeWorker, NativeWorkerError, Response
from app.stt.reazonspeech_model import cached_reazonspeech_snapshot


class InputDiscontinuityError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("音声入力の欠落を検出したため、文字起こしを停止しました。会議を停止して再準備してください。")


@final
class NativeSttPipeline:
    def __init__(
        self,
        stt_queue: queue.Queue[AudioFrame | None],
        cfg: SttConfig,
        role: str,
        broadcast_fn: OutgoingBroadcastFn,
        handle_speech_fn: HandleSpeechFn,
    ) -> None:
        if cfg.sample_rate != 16000 or cfg.vad_engine != "silero" or role not in {"self", "other"}:
            raise ValueError("Rust ReazonSpeechはSileroと16 kHz mono PCMを必要とします。")
        self._queue = stt_queue
        self._cfg = cfg
        self._role = role
        self._broadcast = broadcast_fn
        self._handle_speech = handle_speech_fn
        self._worker: NativeWorker | None = None
        self._task: asyncio.Task[None] | None = None
        self._cleanup: asyncio.Task[None] | None = None
        self._ready = False
        self._stopping = False
        self._tail: list[AudioFrame] = []
        self._generation = 0
        self._last_end = 0
        self._input_sequence: int | None = None
        self.on_ready: Callable[[], Coroutine[Never, Never, None]] | None = None
        self.on_error: Callable[[Exception], Coroutine[Never, Never, None]] | None = None

    def supports_prewarm(self) -> bool:
        return True

    def initialize(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = loop.create_task(self._prepare())

    async def _prepare(self) -> None:
        try:
            if self._cleanup is not None:
                await self._cleanup
            if self._worker is not None:
                await self._worker.close()
            self._ready = False
            executable = os.environ.get("MEETING_REAZON_WORKER")
            model = await asyncio.to_thread(cached_reazonspeech_snapshot)
            if not executable or not Path(executable).is_absolute() or model is None:
                raise NativeWorkerError()
            punctuation = os.environ.get("MEETING_REAZON_PUNCTUATION")
            self._worker = NativeWorker(Path(executable), Path(model), Path(punctuation) if punctuation else None)
            await self._worker.open()
            _ = await self._worker.request({"op": "prepare"}, "prepared", timeout=120)
            _ = await self._worker.request(
                {
                    "op": "configure",
                    "vad_threshold": min(max(self._cfg.vad_sensitivity, 0.05), 0.95),
                    "silence_seconds": self._cfg.silence_duration,
                    "min_voiced_ms": self._cfg.min_voiced_ms,
                    "min_voiced_ratio": self._cfg.min_voiced_ratio,
                    "min_rms_dbfs": self._cfg.min_rms_dbfs,
                },
                "configured",
            )
            self._generation = 0
            self._ready = True
            self._task = None
            callback, self.on_ready = self.on_ready, None
            if callback is not None:
                await callback()
        except asyncio.CancelledError:
            if self._worker is not None:
                await self._worker.close()
            raise
        except Exception:
            self._ready = False
            if self._worker is not None:
                await self._worker.close()
            self._task = None
            callback, self.on_error = self.on_error, None
            self.on_ready = None
            if callback is not None:
                await callback(NativeWorkerError())
            else:
                await self._broadcast(ErrorMsg(text=str(NativeWorkerError())))

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if not self._ready or self._worker is None:
            raise NativeWorkerError()
        if self._task is not None and not self._task.done():
            raise NativeWorkerError()
        # Capture remains alive between meetings. Discard only the pre-start backlog.
        _ = self._take_pending()
        self._tail = []
        self._stopping = False
        self._task = loop.create_task(self._run(self._worker))

    def _take_pending(self) -> list[AudioFrame]:
        frames: list[AudioFrame] = []
        for _ in range(self._queue.qsize()):
            try:
                frame = self._queue.get_nowait()
            except queue.Empty:
                break
            if frame is not None:
                frames.append(frame)
        return frames

    async def _run(self, worker: NativeWorker) -> None:
        try:
            _ = await worker.request({"op": "reset", "role": self._role}, "reset")
            self._generation += 1
            self._last_end = 0
            self._input_sequence = None
            while not self._stopping:
                try:
                    frame = self._queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.01)
                    continue
                if frame is None:
                    break
                await self._audio(worker, frame)
            for frame in self._tail:
                await self._audio(worker, frame)
            self._tail = []
            response = await worker.request({"op": "finish", "role": self._role}, "finished")
            await self._accept(response)
        except asyncio.CancelledError:
            self._ready = False
            await worker.close()
            raise
        except Exception as error:
            self._ready = False
            await worker.close()
            message = str(error) if isinstance(error, InputDiscontinuityError) else str(NativeWorkerError())
            await self._broadcast(ErrorMsg(text=message))
            await self._broadcast(SttStateMsg(backend="reazonspeech", initialized=False, initializing=False))
        finally:
            await self._broadcast(SttInterimMsg(role=self._role, text=""))

    async def _audio(self, worker: NativeWorker, frame: AudioFrame) -> None:
        if len(frame.pcm) != 960:
            raise NativeWorkerError()
        if frame.sequence is not None:
            if self._input_sequence is not None and frame.sequence != self._input_sequence + 1:
                raise InputDiscontinuityError()
            self._input_sequence = frame.sequence
        pcm = list(struct.unpack("<480h", frame.pcm))
        response = await worker.request({"op": "audio", "role": self._role, "pcm": pcm}, "audio")
        await self._accept(response)

    async def _accept(self, response: Response) -> None:
        segment = response.segment
        if segment is None:
            return
        if (
            segment.generation != self._generation
            or segment.end_sample < segment.start_sample
            or segment.end_sample <= self._last_end
        ):
            raise NativeWorkerError()
        self._last_end = segment.end_sample
        recognition = segment.recognition
        if recognition.status != "recognized":
            return
        if recognition.text is None:
            raise NativeWorkerError()
        text = recognition.text
        punctuation = recognition.punctuation
        if punctuation is not None:
            if punctuation.status == "applied":
                if punctuation.text is None:
                    raise NativeWorkerError()
                text = punctuation.text
            else:
                await self._broadcast(ErrorMsg(text="句読点処理に失敗したため、認識した原文を使用します。"))
        if text.strip():
            # Await the existing conversation/history handoff before meeting completion.
            await self._handle_speech(self._role, text)

    def stop(self) -> None:
        if not self._stopping:
            self._stopping = True
            self._tail = self._take_pending()

    async def stop_and_drain(self) -> None:
        self.stop()
        if self._task is not None:
            try:
                async with asyncio.timeout(10):
                    await self._task
            except TimeoutError:
                raise NativeWorkerError() from None
            if not self._ready:
                raise NativeWorkerError()

    def shutdown(self) -> None:
        self._ready = False
        self.on_ready = None
        self.on_error = None
        if self._task is not None:
            _ = self._task.cancel()
        if self._worker is not None:
            self._worker.kill()
            self._cleanup = asyncio.get_running_loop().create_task(self._worker.close())

    async def shutdown_and_wait(self) -> None:
        self.shutdown()
        if self._task is not None:
            _ = await asyncio.gather(self._task, return_exceptions=True)
        if self._cleanup is not None:
            await self._cleanup
