# pyright: reportUninitializedInstanceVariable=false
import asyncio
import queue
import unittest
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Never, cast, override

from app.audio.base import AudioFrame, RecordingResult
from app.core.messages import OutgoingMessage
from app.core.protocols import AudioPipelineLike, SttStreamLike
from app.services.config_loader import ConfigLoader
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
    def __init__(self, role: str, *, fail_on_start: bool = False) -> None:
        self.role: str = role
        self.fail_on_start: bool = fail_on_start
        self.start_calls: int = 0
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
        self.start_calls += 1
        if self.fail_on_start:
            raise RuntimeError(f"{self.role} start failed")
        self.started = True
        self.stopped = False

    def stop(self) -> None:
        self.started = False
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
    audio_lifecycle_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

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
    broadcasts: list[dict[str, object]]
    controller: SttController

    @override
    async def asyncSetUp(self) -> None:
        self.state = FakeState()
        self.created_audios = []
        self.broadcasts = []

        async def broadcast(msg: OutgoingMessage) -> None:
            self.broadcasts.append(cast(dict[str, object], msg.model_dump()))

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

    async def test_config_change_reloads_complete_audio_subsystem(self) -> None:
        await self.controller.start_level_monitors()
        first_batch = list(self.created_audios)
        old_other = FakeSttStream("other", prewarm=True)
        old_self = FakeSttStream("self", prewarm=True)
        self.state.stt_other = old_other
        self.state.stt_self = old_self
        self.state.stt_initialized = True
        old = cast(ConfigLoader, cast(object, SimpleNamespace(stt_backend="deepgram", stt_config=object())))
        new = cast(ConfigLoader, cast(object, SimpleNamespace(stt_backend="whisper", stt_config=object())))

        await self.controller.on_config_changed(old, new)

        self.assertTrue(old_other.shutdown_called)
        self.assertTrue(old_self.shutdown_called)
        self.assertTrue(all(audio.stopped for audio in first_batch))
        new_audios = [audio for audio in self.created_audios if audio not in first_batch]
        self.assertEqual({audio.role for audio in new_audios}, {"other", "self"})
        self.assertTrue(all(audio.started for audio in new_audios))
        self.assertIsNone(self.state.stt_other)
        self.assertIsNone(self.state.stt_self)

    async def test_config_change_is_deferred_without_touching_active_meeting_audio(self) -> None:
        await self.controller.start_level_monitors()
        first_batch = list(self.created_audios)
        old_other = FakeSttStream("other", prewarm=True)
        old_self = FakeSttStream("self", prewarm=True)
        self.state.stt_other = old_other
        self.state.stt_self = old_self
        self.state.stt_initialized = True
        self.state._is_running = True  # pyright: ignore[reportPrivateUsage]
        old = cast(ConfigLoader, cast(object, SimpleNamespace(stt_backend="deepgram", stt_config=object())))
        new = cast(ConfigLoader, cast(object, SimpleNamespace(stt_backend="whisper", stt_config=object())))

        await self.controller.on_config_changed(old, new)

        self.assertEqual(self.controller.pending_audio_reload_backend, "whisper")
        self.assertEqual(self.controller.backend, "deepgram")
        self.assertFalse(old_other.shutdown_called)
        self.assertFalse(old_self.shutdown_called)
        self.assertTrue(all(not audio.stopped for audio in first_batch))
        self.assertEqual(self.created_audios, first_batch)

        self.state._is_running = False  # pyright: ignore[reportPrivateUsage]
        applied = await self.controller.apply_pending_audio_reload()

        self.assertTrue(applied)
        self.assertIsNone(self.controller.pending_audio_reload_backend)
        self.assertEqual(self.controller.backend, "whisper")
        self.assertTrue(old_other.shutdown_called)
        self.assertTrue(old_self.shutdown_called)
        self.assertTrue(all(audio.stopped for audio in first_batch))
        replacements = [audio for audio in self.created_audios if audio not in first_batch]
        self.assertEqual({audio.role for audio in replacements}, {"other", "self"})

    async def test_failed_replacement_restores_prewarmed_state_and_retains_pending_reload(self) -> None:
        created: list[FakeAudioPipeline] = []
        replacement_mode = False

        def make_audio(device: int | str | None, role: str) -> FakeAudioPipeline:
            _ = device
            pipeline = FakeAudioPipeline(role, fail_on_start=replacement_mode and role == "self")
            created.append(pipeline)
            return pipeline

        async def broadcast(msg: OutgoingMessage) -> None:
            self.broadcasts.append(cast(dict[str, object], msg.model_dump()))

        controller = SttController(
            state=self.state,
            backend="deepgram",
            make_audio=make_audio,
            make_stt=_make_stt_factory([], prewarm=True),
            get_input_devices=lambda: [],
            broadcast=broadcast,
        )
        await controller.start_level_monitors()
        old_other_audio = controller.audio_other
        old_self_audio = controller.audio_self
        old_other_stt = FakeSttStream("other", prewarm=True)
        old_self_stt = FakeSttStream("self", prewarm=True)
        self.state.stt_other = old_other_stt
        self.state.stt_self = old_self_stt
        self.state.stt_initialized = True
        replacement_mode = True
        old = cast(ConfigLoader, cast(object, SimpleNamespace(stt_backend="deepgram", stt_config=object())))
        new = cast(ConfigLoader, cast(object, SimpleNamespace(stt_backend="whisper", stt_config=object())))

        await controller.on_config_changed(old, new)

        self.assertIs(controller.audio_other, old_other_audio)
        self.assertIs(controller.audio_self, old_self_audio)
        self.assertIs(self.state.stt_other, old_other_stt)
        self.assertIs(self.state.stt_self, old_self_stt)
        self.assertTrue(self.state.stt_initialized)
        self.assertFalse(self.state.stt_initializing)
        self.assertTrue(old_other_stt.stopped)
        self.assertTrue(old_self_stt.stopped)
        self.assertFalse(old_other_stt.shutdown_called)
        self.assertFalse(old_self_stt.shutdown_called)
        self.assertEqual(controller.backend, "deepgram")
        self.assertEqual(controller.pending_audio_reload_backend, "whisper")
        self.assertEqual(cast(FakeAudioPipeline, old_other_audio).start_calls, 2)
        self.assertEqual(cast(FakeAudioPipeline, old_self_audio).start_calls, 2)
        self.assertTrue(cast(FakeAudioPipeline, old_other_audio).started)
        self.assertTrue(cast(FakeAudioPipeline, old_self_audio).started)
        replacements = created[2:]
        self.assertEqual({pipeline.role for pipeline in replacements}, {"other", "self"})
        self.assertTrue(all(pipeline.stopped for pipeline in replacements))
        self.assertTrue(any(message.get("type") == "error" for message in self.broadcasts))

    async def test_pending_reload_marker_clears_only_after_successful_retry(self) -> None:
        created: list[FakeAudioPipeline] = []
        fail_replacement = False

        def make_audio(device: int | str | None, role: str) -> FakeAudioPipeline:
            _ = device
            pipeline = FakeAudioPipeline(role, fail_on_start=fail_replacement and role == "self")
            created.append(pipeline)
            return pipeline

        async def broadcast(msg: OutgoingMessage) -> None:
            self.broadcasts.append(cast(dict[str, object], msg.model_dump()))

        controller = SttController(
            state=self.state,
            backend="deepgram",
            make_audio=make_audio,
            make_stt=_make_stt_factory([], prewarm=True),
            get_input_devices=lambda: [],
            broadcast=broadcast,
        )
        await controller.start_level_monitors()
        old_other_stt = FakeSttStream("other", prewarm=True)
        old_self_stt = FakeSttStream("self", prewarm=True)
        self.state.stt_other = old_other_stt
        self.state.stt_self = old_self_stt
        self.state.stt_initialized = True
        self.state._is_running = True  # pyright: ignore[reportPrivateUsage]
        old = cast(ConfigLoader, cast(object, SimpleNamespace(stt_backend="deepgram", stt_config=object())))
        new = cast(ConfigLoader, cast(object, SimpleNamespace(stt_backend="whisper", stt_config=object())))
        await controller.on_config_changed(old, new)

        self.state._is_running = False  # pyright: ignore[reportPrivateUsage]
        fail_replacement = True
        first_applied = await controller.apply_pending_audio_reload()

        self.assertFalse(first_applied)
        self.assertEqual(controller.pending_audio_reload_backend, "whisper")
        self.assertEqual(controller.backend, "deepgram")
        self.assertIs(self.state.stt_other, old_other_stt)
        self.assertIs(self.state.stt_self, old_self_stt)
        self.assertTrue(self.state.stt_initialized)
        self.assertFalse(old_other_stt.shutdown_called)
        self.assertFalse(old_self_stt.shutdown_called)

        fail_replacement = False
        second_applied = await controller.apply_pending_audio_reload()

        self.assertTrue(second_applied)
        self.assertIsNone(controller.pending_audio_reload_backend)
        self.assertEqual(controller.backend, "whisper")
        self.assertTrue(old_other_stt.shutdown_called)
        self.assertTrue(old_self_stt.shutdown_called)
        self.assertIsNone(self.state.stt_other)
        self.assertIsNone(self.state.stt_self)

    async def test_failed_device_reload_gates_orphan_bound_stt_until_restored_device_rebuild(self) -> None:
        class RestartQueueAudioPipeline(FakeAudioPipeline):
            @override
            def start(self, loop: asyncio.AbstractEventLoop) -> None:
                if self.start_calls:
                    self._stt_queue: queue.Queue[AudioFrame | None] = queue.Queue()
                super().start(loop)

        class QueueBoundSttStream(FakeSttStream):
            def __init__(self, role: str, audio: RestartQueueAudioPipeline) -> None:
                super().__init__(role, prewarm=True)
                self.audio: RestartQueueAudioPipeline = audio
                self.bound_queue: queue.Queue[AudioFrame | None] = audio.stt_queue

        created: list[tuple[int | str | None, RestartQueueAudioPipeline]] = []
        fail_replacement = False

        def make_audio(device: int | str | None, role: str) -> RestartQueueAudioPipeline:
            pipeline = RestartQueueAudioPipeline(
                role,
                fail_on_start=fail_replacement and role == "self",
            )
            created.append((device, pipeline))
            return pipeline

        async def broadcast(msg: OutgoingMessage) -> None:
            self.broadcasts.append(cast(dict[str, object], msg.model_dump()))

        controller = SttController(
            state=self.state,
            backend="deepgram",
            make_audio=make_audio,
            make_stt=_make_stt_factory([], prewarm=True),
            get_input_devices=lambda: [],
            broadcast=broadcast,
        )
        await controller.start_level_monitors()
        old_other_audio = cast(RestartQueueAudioPipeline, controller.audio_other)
        old_self_audio = cast(RestartQueueAudioPipeline, controller.audio_self)
        old_other_stt = QueueBoundSttStream("other", old_other_audio)
        old_self_stt = QueueBoundSttStream("self", old_self_audio)
        self.state.stt_other = old_other_stt
        self.state.stt_self = old_self_stt
        self.state.stt_initialized = True

        fail_replacement = True
        await controller.set_device("self", 7)

        self.assertIsNone(self.state.device_self)
        self.assertEqual(controller.pending_audio_reload_backend, "deepgram")
        self.assertIs(self.state.stt_other, old_other_stt)
        self.assertIs(self.state.stt_self, old_self_stt)
        self.assertIsNot(old_other_stt.bound_queue, old_other_audio.stt_queue)
        self.assertIsNot(old_self_stt.bound_queue, old_self_audio.stt_queue)
        self.assertFalse(await controller.apply_pending_audio_reload())
        self.assertEqual(controller.pending_audio_reload_backend, "deepgram")
        self.assertFalse(old_other_stt.started)
        self.assertFalse(old_self_stt.started)

        fail_replacement = False
        self.assertTrue(await controller.apply_pending_audio_reload())

        self.assertIsNone(controller.pending_audio_reload_backend)
        self.assertIsNone(self.state.stt_other)
        self.assertIsNone(self.state.stt_self)
        self.assertTrue(old_other_stt.shutdown_called)
        self.assertTrue(old_self_stt.shutdown_called)
        self.assertEqual([device for device, _ in created[-2:]], [None, None])

    async def test_device_change_is_rejected_during_meeting(self) -> None:
        await self.controller.start_level_monitors()
        first_batch = list(self.created_audios)
        self.state._is_running = True  # pyright: ignore[reportPrivateUsage]

        await self.controller.set_device("self", 1)

        self.assertIsNone(self.state.device_self)
        self.assertEqual(self.created_audios, first_batch)
        self.assertTrue(
            any(
                message.get("type") == "error" and "会議中は音声デバイスを変更できません" in str(message.get("text"))
                for message in self.broadcasts
            )
        )


if __name__ == "__main__":
    _ = unittest.main()
