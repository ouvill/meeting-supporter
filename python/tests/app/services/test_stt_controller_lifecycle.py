# pyright: reportUninitializedInstanceVariable=false
import asyncio
import queue
import unittest
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Never, cast, override

from app.audio.base import AudioFrame, RecordingResult
from app.core.config import SttConfig
from app.core.messages import OutgoingMessage
from app.core.protocols import AudioPipelineLike, SttStreamLike
from app.services.stt_controller import SttController


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0.01)


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[object] = []

    async def send_json(self, data: object) -> None:
        self.messages.append(data)


class FakeSttStream:
    def __init__(self, role: str, prewarm: bool = True) -> None:
        self.role: str = role
        self._prewarm: bool = prewarm
        self.on_ready: Callable[[], Coroutine[Never, Never, None]] | None = None
        self.on_error: Callable[[Exception], Coroutine[Never, Never, None]] | None = None
        self.initialized: bool = False
        self.started: bool = False
        self.stopped: bool = False
        self.shutdown_called: bool = False

    def supports_prewarm(self) -> bool:
        return self._prewarm

    def initialize(self, loop: asyncio.AbstractEventLoop) -> None:
        self.initialized = True
        if self.on_ready:
            _ = loop.create_task(self.on_ready())
            self.on_ready = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:  # pyright: ignore[reportUnusedParameter]
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def apply_config(self, cfg: SttConfig) -> None:  # pyright: ignore[reportUnusedParameter]
        pass


class HangingInitSttStream(FakeSttStream):
    @override
    def initialize(self, loop: asyncio.AbstractEventLoop) -> None:
        _ = loop
        self.initialized: bool = True


class FailingInitSttStream(FakeSttStream):
    @override
    def initialize(self, loop: asyncio.AbstractEventLoop) -> None:
        self.initialized: bool = True
        callback = self.on_error
        if callback is not None:
            _ = loop.create_task(callback(RuntimeError(f"{self.role} load failed")))
            self.on_error: Callable[[Exception], Coroutine[Never, Never, None]] | None = None


class FakeAudioPipeline:
    def __init__(self, role: str) -> None:
        self.role: str = role
        self.started: bool = False
        self.stopped: bool = False
        self.flushed: bool = False
        self._stt_queue: queue.Queue[AudioFrame | None] = queue.Queue()
        self._recording_queue: queue.Queue[AudioFrame | None] = queue.Queue()

    @property
    def stt_queue(self) -> queue.Queue[AudioFrame | None]:
        return self._stt_queue

    @property
    def recording_queue(self) -> queue.Queue[AudioFrame | None]:
        return self._recording_queue

    def flush_stt_queue(self) -> None:
        self.flushed = True

    def start(self, loop: asyncio.AbstractEventLoop) -> None:  # pyright: ignore[reportUnusedParameter]
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def start_recording(self, path: "Path") -> None:  # pyright: ignore[reportUnusedParameter]
        pass

    def stop_recording(self) -> RecordingResult | None:
        return None


@dataclass
class FakeState:
    _is_running: bool = False
    stt_other: SttStreamLike | None = None
    stt_self: SttStreamLike | None = None
    stt_initialized: bool = False
    stt_initializing: bool = False
    device_other: int | str | None = None
    device_self: int | str | None = None

    @property
    def is_running(self) -> bool:
        return self._is_running


def _make_audio_factory(created: list[FakeAudioPipeline]):
    def make_audio(device: int | str | None, role: str) -> FakeAudioPipeline:  # pyright: ignore[reportUnusedParameter]
        a = FakeAudioPipeline(role=role)
        created.append(a)
        return a

    return make_audio


def _make_stt_factory(created: list[FakeSttStream], prewarm: bool = True):
    def make_stt(audio: AudioPipelineLike, role: str) -> FakeSttStream:  # pyright: ignore[reportUnusedParameter]
        s = FakeSttStream(role=role, prewarm=prewarm)
        created.append(s)
        return s

    return make_stt


class SttControllerLifecycleTest(unittest.IsolatedAsyncioTestCase):
    state: FakeState
    broadcasts: list[dict[str, object]]
    created_audios: list[FakeAudioPipeline]
    created_stts: list[FakeSttStream]
    controller: SttController

    @override
    async def asyncSetUp(self) -> None:
        self.state = FakeState()
        self.broadcasts = []
        self.created_audios = []
        self.created_stts = []

        async def broadcast(msg: OutgoingMessage) -> None:
            self.broadcasts.append(cast(dict[str, object], msg.model_dump()))

        self.controller = SttController(
            state=self.state,
            backend="whisper",
            make_audio=_make_audio_factory(self.created_audios),
            make_stt=_make_stt_factory(self.created_stts, prewarm=True),
            get_input_devices=lambda: [],
            broadcast=broadcast,
        )
        await self.controller.start_level_monitors()

    async def test_init_stt_marks_initialized_after_both_streams_ready(self) -> None:
        await self.controller.init_stt()
        await asyncio.sleep(0)

        self.assertIsNotNone(self.state.stt_other)
        self.assertIsNotNone(self.state.stt_self)
        self.assertTrue(self.state.stt_initialized)
        self.assertFalse(self.state.stt_initializing)
        self.assertIn(
            {
                "type": "stt_state",
                "backend": "whisper",
                "initialized": True,
                "initializing": False,
            },
            self.broadcasts,
        )

    async def test_init_stt_initialize_failure_resets_loading_state(self) -> None:
        broadcasts: list[dict[str, object]] = []

        async def broadcast(msg: OutgoingMessage) -> None:
            broadcasts.append(cast(dict[str, object], msg.model_dump()))

        state = FakeState()

        def make_stt(_audio: AudioPipelineLike, role: str) -> FakeSttStream:
            if role == "other":
                return FailingInitSttStream(role)
            return FakeSttStream(role)

        controller = SttController(
            state=state,
            backend="whisper",
            make_audio=_make_audio_factory([]),
            make_stt=make_stt,
            get_input_devices=lambda: [],
            broadcast=broadcast,
        )
        await controller.start_level_monitors()
        await controller.init_stt()
        await asyncio.sleep(0)

        self.assertFalse(state.stt_initialized)
        self.assertFalse(state.stt_initializing)
        self.assertIsNone(state.stt_other)
        self.assertIsNone(state.stt_self)
        self.assertIn(
            {"type": "error", "text": "音声認識の準備に失敗しました: other load failed"},
            broadcasts,
        )
        self.assertIn(
            {
                "type": "stt_state",
                "backend": "whisper",
                "initialized": False,
                "initializing": False,
            },
            broadcasts,
        )

    async def test_default_init_timeout_keeps_hanging_prewarm_initializing(self) -> None:
        broadcasts: list[dict[str, object]] = []
        created_stts: list[FakeSttStream] = []

        async def broadcast(msg: OutgoingMessage) -> None:
            broadcasts.append(cast(dict[str, object], msg.model_dump()))

        state = FakeState()

        def make_stt(_audio: AudioPipelineLike, role: str) -> FakeSttStream:
            s = HangingInitSttStream(role)
            created_stts.append(s)
            return s

        controller = SttController(
            state=state,
            backend="whisper",
            make_audio=_make_audio_factory([]),
            make_stt=make_stt,
            get_input_devices=lambda: [],
            broadcast=broadcast,
        )
        await controller.start_level_monitors()
        await controller.init_stt()
        await _wait_until(lambda: state.stt_initializing and len(created_stts) == 2)

        self.assertFalse(state.stt_initialized)
        self.assertTrue(state.stt_initializing)
        self.assertIs(state.stt_other, created_stts[0])
        self.assertIs(state.stt_self, created_stts[1])
        self.assertFalse(any(stream.shutdown_called for stream in created_stts))
        self.assertNotIn(
            {
                "type": "stt_state",
                "backend": "whisper",
                "initialized": False,
                "initializing": False,
            },
            broadcasts,
        )
        self.assertFalse(any(msg.get("type") == "error" for msg in broadcasts))

    async def test_shutdown_during_initialization_ignores_late_ready_callbacks(self) -> None:
        broadcasts: list[dict[str, object]] = []
        created_stts: list[FakeSttStream] = []

        async def broadcast(msg: OutgoingMessage) -> None:
            broadcasts.append(cast(dict[str, object], msg.model_dump()))

        state = FakeState()

        def make_stt(_audio: AudioPipelineLike, role: str) -> FakeSttStream:
            s = HangingInitSttStream(role)
            created_stts.append(s)
            return s

        controller = SttController(
            state=state,
            backend="whisper",
            make_audio=_make_audio_factory([]),
            make_stt=make_stt,
            get_input_devices=lambda: [],
            broadcast=broadcast,
        )
        await controller.start_level_monitors()
        await controller.init_stt()

        old_callbacks = [stream.on_ready for stream in created_stts]
        await controller.shutdown_stt()
        for callback in old_callbacks:
            if callback is not None:
                await callback()

        self.assertFalse(state.stt_initialized)
        self.assertFalse(state.stt_initializing)
        self.assertIsNone(state.stt_other)
        self.assertIsNone(state.stt_self)
        self.assertNotIn(
            {
                "type": "stt_state",
                "backend": "whisper",
                "initialized": True,
                "initializing": False,
            },
            broadcasts,
        )

    async def test_init_stt_timeout_resets_loading_state_and_allows_retry(self) -> None:
        broadcasts: list[dict[str, object]] = []
        created_stts: list[FakeSttStream] = []

        async def broadcast(msg: OutgoingMessage) -> None:
            broadcasts.append(cast(dict[str, object], msg.model_dump()))

        state = FakeState()

        def make_stt(_audio: AudioPipelineLike, role: str) -> FakeSttStream:
            s = HangingInitSttStream(role)
            created_stts.append(s)
            return s

        controller = SttController(
            state=state,
            backend="whisper",
            make_audio=_make_audio_factory([]),
            make_stt=make_stt,
            get_input_devices=lambda: [],
            broadcast=broadcast,
            init_timeout_seconds=0.01,
        )
        await controller.start_level_monitors()
        await controller.init_stt()
        await _wait_until(lambda: not state.stt_initializing and state.stt_other is None and state.stt_self is None)

        self.assertFalse(state.stt_initialized)
        self.assertFalse(state.stt_initializing)
        self.assertIsNone(state.stt_other)
        self.assertIsNone(state.stt_self)
        self.assertTrue(all(stream.shutdown_called for stream in created_stts))
        self.assertIn(
            {
                "type": "error",
                "text": (
                    "音声認識の準備が0.01秒以内に完了しませんでした。デバイスとモデル設定を確認して再試行してください"
                ),
            },
            broadcasts,
        )
        self.assertIn(
            {
                "type": "stt_state",
                "backend": "whisper",
                "initialized": False,
                "initializing": False,
            },
            broadcasts,
        )

    async def test_start_meeting_requires_initialized_when_prewarm(self) -> None:
        ws = FakeWebSocket()
        result = await self.controller.start_meeting(ws)

        self.assertFalse(result)
        self.assertFalse(self.state.is_running)
        self.assertEqual(
            [{"type": "error", "text": "先に音声認識を準備してください"}],
            ws.messages,
        )

    async def test_start_meeting_returns_false_when_already_running(self) -> None:
        """start_meeting returns False when already running and
        session_already_started is not set."""
        await self.controller.init_stt()
        await asyncio.sleep(0)

        self.state._is_running = True  # pyright: ignore[reportPrivateUsage]
        ws = FakeWebSocket()
        result = await self.controller.start_meeting(ws)

        self.assertFalse(result)
        self.assertIn(
            {"type": "status", "text": "すでに会議中です"},
            ws.messages,
        )

    async def test_start_meeting_success_returns_true(self) -> None:
        """start_meeting returns True on successful start."""
        await self.controller.init_stt()
        await asyncio.sleep(0)

        # Reset and use session_already_started to skip is_running check
        ws = FakeWebSocket()
        result = await self.controller.start_meeting(ws, session_already_started=True)

        self.assertTrue(result)
        assert isinstance(self.state.stt_other, FakeSttStream)
        assert isinstance(self.state.stt_self, FakeSttStream)
        self.assertTrue(self.state.stt_other.started)
        self.assertTrue(self.state.stt_self.started)

    async def test_stop_and_shutdown_reset_state(self) -> None:
        await self.controller.init_stt()
        await asyncio.sleep(0)

        ws = FakeWebSocket()
        result = await self.controller.start_meeting(ws, session_already_started=True)
        self.assertTrue(result)

        await self.controller.stop_meeting()
        assert isinstance(self.state.stt_other, FakeSttStream)
        assert isinstance(self.state.stt_self, FakeSttStream)
        self.assertTrue(self.state.stt_other.stopped)
        self.assertTrue(self.state.stt_self.stopped)

        await self.controller.shutdown_stt()
        self.assertIsNone(self.state.stt_other)
        self.assertIsNone(self.state.stt_self)
        self.assertFalse(self.state.stt_initialized)
        self.assertFalse(self.state.stt_initializing)

    async def test_init_stt_creation_failure_is_reported(self) -> None:
        broadcasts: list[dict[str, object]] = []

        async def broadcast(msg: OutgoingMessage) -> None:
            broadcasts.append(cast(dict[str, object], msg.model_dump()))

        state = FakeState()

        def make_stt_fail(_audio: AudioPipelineLike, _role: str) -> FakeSttStream:
            raise ValueError("unsupported backend")

        controller = SttController(
            state=state,
            backend="whisper",
            make_audio=_make_audio_factory([]),
            make_stt=make_stt_fail,
            get_input_devices=lambda: [],
            broadcast=broadcast,
        )
        await controller.start_level_monitors()
        await controller.init_stt()

        self.assertFalse(state.stt_initialized)
        self.assertFalse(state.stt_initializing)
        self.assertIn(
            {"type": "error", "text": "音声認識の準備に失敗しました: unsupported backend"},
            broadcasts,
        )

    async def test_init_stt_self_creation_failure_emits_not_initializing_state(self) -> None:
        broadcasts: list[dict[str, object]] = []

        async def broadcast(msg: OutgoingMessage) -> None:
            broadcasts.append(cast(dict[str, object], msg.model_dump()))

        state = FakeState()

        def make_stt(_audio: AudioPipelineLike, role: str) -> FakeSttStream:
            if role == "self":
                raise ValueError("self unsupported")
            return FakeSttStream(role)

        controller = SttController(
            state=state,
            backend="whisper",
            make_audio=_make_audio_factory([]),
            make_stt=make_stt,
            get_input_devices=lambda: [],
            broadcast=broadcast,
        )
        await controller.start_level_monitors()
        await controller.init_stt()

        self.assertFalse(state.stt_initialized)
        self.assertFalse(state.stt_initializing)
        self.assertIn(
            {"type": "error", "text": "音声認識の準備に失敗しました: self unsupported"},
            broadcasts,
        )
        self.assertIn(
            {
                "type": "stt_state",
                "backend": "whisper",
                "initialized": False,
                "initializing": False,
            },
            broadcasts,
        )

    async def test_start_meeting_creation_failure_is_reported(self) -> None:
        broadcasts: list[dict[str, object]] = []

        async def broadcast(msg: OutgoingMessage) -> None:
            broadcasts.append(cast(dict[str, object], msg.model_dump()))

        state = FakeState()

        def make_stt_fail(_audio: AudioPipelineLike, _role: str) -> FakeSttStream:
            raise ValueError("unsupported backend")

        controller = SttController(
            state=state,
            backend="deepgram",
            make_audio=_make_audio_factory([]),
            make_stt=make_stt_fail,
            get_input_devices=lambda: [],
            broadcast=broadcast,
        )
        await controller.start_level_monitors()

        ws = FakeWebSocket()
        result = await controller.start_meeting(ws)

        self.assertFalse(result)
        self.assertFalse(state._is_running)  # pyright: ignore[reportPrivateUsage]
        self.assertEqual(
            {"type": "error", "text": "音声認識の開始準備に失敗しました: unsupported backend"},
            ws.messages[0],
        )
        self.assertIn(
            {"type": "error", "text": "音声認識の開始準備に失敗しました: unsupported backend"},
            broadcasts,
        )

    async def test_start_meeting_creation_failure_with_session_flag(self) -> None:
        """Returns False on creation failure even with session_already_started=True."""
        broadcasts: list[dict[str, object]] = []

        async def broadcast(msg: OutgoingMessage) -> None:
            broadcasts.append(cast(dict[str, object], msg.model_dump()))

        state = FakeState()

        def make_stt_fail(_audio: AudioPipelineLike, _role: str) -> FakeSttStream:
            raise ValueError("unsupported backend")

        controller = SttController(
            state=state,
            backend="deepgram",
            make_audio=_make_audio_factory([]),
            make_stt=make_stt_fail,
            get_input_devices=lambda: [],
            broadcast=broadcast,
        )
        await controller.start_level_monitors()

        ws = FakeWebSocket()
        result = await controller.start_meeting(ws, session_already_started=True)

        self.assertFalse(result)
        self.assertIn(
            {"type": "error", "text": "音声認識の開始準備に失敗しました: unsupported backend"},
            broadcasts,
        )


class AudioPipelineLifecycleTest(unittest.IsolatedAsyncioTestCase):
    state: FakeState
    created_audios: list[FakeAudioPipeline]
    controller: SttController

    @override
    async def asyncSetUp(self) -> None:
        self.state = FakeState()
        self.created_audios = []

        async def broadcast(_msg: OutgoingMessage) -> None:
            pass

        self.controller = SttController(
            state=self.state,
            backend="deepgram",
            make_audio=_make_audio_factory(self.created_audios),
            make_stt=_make_stt_factory([], prewarm=False),
            get_input_devices=lambda: [],
            broadcast=broadcast,
        )

    async def test_start_level_monitors_starts_both_roles(self) -> None:
        await self.controller.start_level_monitors()

        roles = {a.role for a in self.created_audios}
        self.assertEqual(roles, {"other", "self"})
        for a in self.created_audios:
            self.assertTrue(a.started)

    async def test_stop_level_monitors_stops_running_pipelines(self) -> None:
        await self.controller.start_level_monitors()
        self.controller.stop_level_monitors()

        for a in self.created_audios:
            self.assertTrue(a.stopped)

    async def test_start_level_monitors_restarts_on_device_change(self) -> None:
        await self.controller.start_level_monitors()
        first_batch = list(self.created_audios)

        await self.controller.set_device("self", 1)

        for a in first_batch:
            self.assertTrue(a.stopped)

        new_audios = [a for a in self.created_audios if a not in first_batch]
        self.assertTrue(len(new_audios) > 0)
        for a in new_audios:
            self.assertTrue(a.started)


if __name__ == "__main__":
    _ = unittest.main()
