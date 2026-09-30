# pyright: reportUninitializedInstanceVariable=false
"""Tests for lifespan shutdown — coordinator path vs direct STT stop."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from app.core.config import AiRouteAssignments
from app.lifespan import create_lifespan

if TYPE_CHECKING:
    from fastapi import FastAPI


class _RecordingSttController:
    """Records all calls made to SttController during shutdown."""

    _events: list[str]
    stop_meeting_called: bool
    shutdown_stt_called: bool
    stop_level_monitors_called: bool

    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.stop_meeting_called = False
        self.shutdown_stt_called = False
        self.stop_level_monitors_called = False

    async def stop_meeting(self) -> None:
        self.stop_meeting_called = True

    async def shutdown_stt(self) -> None:
        self.shutdown_stt_called = True
        self._events.append("stt.shutdown")

    def stop_level_monitors(self) -> None:
        self.stop_level_monitors_called = True


class _RecordingMeetingLifecycle:
    """Records whether stop_meeting was called via the coordinator."""

    stop_meeting_called: bool

    def __init__(self) -> None:
        self.stop_meeting_called = False

    async def stop_meeting(self) -> None:
        self.stop_meeting_called = True

    async def close(self) -> None:
        pass


class _DummyConfig:
    """Minimal config stub for create_lifespan."""

    ai_assignments: AiRouteAssignments = AiRouteAssignments()
    stt_backend: str = "whisper"
    context_dir: Path = Path("/nonexistent/context")
    user_data_dir: Path = Path("/nonexistent/user_data")
    settings_store: object = SimpleNamespace(
        config_path=Path("/nonexistent/config.toml"),
        default_config_path=Path("/nonexistent/default.toml"),
    )
    agent_settings: dict[str, bool] = {}
    stt_config: object = SimpleNamespace(
        remote_url="",
        remote_token="",
        whisper_model="base",
        language="ja",
        device="cpu",
        vad_engine="silero",
        vad_sensitivity=0.4,
        vad_aggressiveness=2,
        silence_duration=1.0,
        min_voiced_ms=0,
        min_voiced_ratio=0.0,
        deepgram_model="nova-2",
        hard_min_voiced_ms=120,
        soft_min_voiced_ms=240,
        soft_min_voiced_ratio=0.35,
        soft_no_speech_threshold=0.6,
        soft_logprob_threshold=-1.0,
    )

    def reload(self) -> "_DummyConfig":
        return self


class _DummyState:
    """Minimal state stub."""

    device_other: object = None
    context_text: str = ""


class _RecordingHistoryRepository:
    def __init__(self, events: list[str]) -> None:
        self._events: list[str] = events

    async def initialize(self) -> None:
        self._events.append("repository.initialize")

    async def close(self) -> None:
        self._events.append("repository.close")


class _RecordingHistoryService:
    def __init__(self, events: list[str]) -> None:
        self._events: list[str] = events

    async def flush_pending(self, timeout: float = 5.0) -> None:
        _ = timeout
        self._events.append("history.flush")


class LifespanShutdownTest(unittest.IsolatedAsyncioTestCase):
    """Verifies shutdown uses the coordinator path when present."""

    async def test_shutdown_calls_coordinator_when_provided(self) -> None:
        """When meeting_lifecycle is given, stop_meeting should use coordinator,
        not stt_controller.stop_meeting directly."""
        events: list[str] = []
        stt = _RecordingSttController(events)
        lifecycle = _RecordingMeetingLifecycle()
        lifespan_fn = create_lifespan(
            stt_controller=stt,  # pyright: ignore[reportArgumentType]
            config=_DummyConfig(),  # pyright: ignore[reportArgumentType]
            state=_DummyState(),  # pyright: ignore[reportArgumentType]
            meeting_lifecycle=lifecycle,  # pyright: ignore[reportArgumentType]
        )

        # Run the lifespan (startup → yield → shutdown)
        async with lifespan_fn(_make_dummy_app()):
            pass

        # Coordinator stop_meeting was called
        self.assertTrue(lifecycle.stop_meeting_called)

        # stt_controller.stop_meeting was NOT called directly
        self.assertFalse(stt.stop_meeting_called)

        # stt_controller.shutdown_stt and stop_level_monitors are still called
        self.assertTrue(stt.shutdown_stt_called)
        self.assertTrue(stt.stop_level_monitors_called)

    async def test_shutdown_falls_back_to_stt_controller(self) -> None:
        """When meeting_lifecycle is NOT given, stt_controller.stop_meeting is
        called directly."""
        events: list[str] = []
        stt = _RecordingSttController(events)

        # All stubs below satisfy the expected protocol structurally.
        lifespan_fn = create_lifespan(
            stt_controller=stt,  # pyright: ignore[reportArgumentType]
            config=_DummyConfig(),  # pyright: ignore[reportArgumentType]
            state=_DummyState(),  # pyright: ignore[reportArgumentType]
        )

        async with lifespan_fn(_make_dummy_app()):
            pass

        # Falls back to direct STT controller stop_meeting
        self.assertTrue(stt.stop_meeting_called)
        self.assertTrue(stt.shutdown_stt_called)
        self.assertTrue(stt.stop_level_monitors_called)

    async def test_shutdown_flushes_history_before_repository_close_without_active_meeting(self) -> None:
        """Shutdown cleans the manager before STT teardown and then drains history."""
        events: list[str] = []
        stt = _RecordingSttController(events)
        lifespan_fn = create_lifespan(
            stt_controller=stt,  # pyright: ignore[reportArgumentType]
            config=_DummyConfig(),  # pyright: ignore[reportArgumentType]
            state=_DummyState(),  # pyright: ignore[reportArgumentType]
            history_repository=_RecordingHistoryRepository(events),  # pyright: ignore[reportArgumentType]
            history_service=_RecordingHistoryService(events),  # pyright: ignore[reportArgumentType]
        )

        async with lifespan_fn(_make_dummy_app()):
            pass

        self.assertEqual(
            ["repository.initialize", "history.flush", "repository.close"],
            [event for event in events if event in {"repository.initialize", "history.flush", "repository.close"}],
        )


def _make_dummy_app() -> "FastAPI":
    """Create a minimal FastAPI instance with only the required attributes."""
    from fastapi import FastAPI

    return FastAPI()


if __name__ == "__main__":
    _ = unittest.main()
