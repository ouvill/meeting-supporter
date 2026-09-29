"""Synthetic subprocess tests for the existing-app Rust speech bridge."""

import asyncio
import os
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import OutgoingMessage
from app.stt.factory import build_pipeline
from app.stt.native_pipeline import NativeSttPipeline
from app.stt.native_worker import NativeWorker, NativeWorkerError

# Runs as a real subprocess; deliberately flushes recognition only on Finish.
_WORKER = """
import json, sys, time
print(json.dumps(dict(id=None, type="ready", protocol=2, transcription_available=True)), flush=True)
generation = 0
samples = 0
for line in sys.stdin:
    request = json.loads(line)
    command = request["command"]
    op = command["op"]
    kinds = dict(prepare="prepared", configure="configured", reset="reset", audio="audio", finish="finished")
    response = dict(id=request["id"], type=kinds[op])
    if op == "reset":
        generation += 1
        samples = 0
    if op == "audio":
        samples += len(command["pcm"])
    if op == "finish" and samples:
        punctuation = dict(status="applied", text="テストです。")
        recognition = dict(status="recognized", text="テストです", punctuation=punctuation)
        response["segment"] = dict(generation=generation, start_sample=0, end_sample=samples, recognition=recognition)
    print(json.dumps(response), flush=True)
"""


def config() -> SttConfig:
    return SttConfig(
        backend="reazonspeech",
        whisper_model="small",
        deepgram_model="nova-3",
        language="ja",
        vad_sensitivity=0.5,
        silence_duration=0.4,
        vad_aggressiveness=2,
        device="auto",
        remote_url="",
        remote_token="",
        sample_rate=16000,
        chunk_size=480,
    )


@unittest.skipIf(os.name == "nt", "Synthetic executable fixture uses a POSIX shebang")
class NativePipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_factory_flush_restart_and_existing_conversation_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "worker"
            _ = executable.write_text(f"#!{sys.executable}\n" + _WORKER)
            executable.chmod(0o700)
            frames: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=200)
            received: list[tuple[str, str]] = []
            messages: list[OutgoingMessage] = []

            async def broadcast(message: OutgoingMessage) -> None:
                messages.append(message)

            async def handle(role: str, text: str) -> None:
                await asyncio.sleep(0.02)  # Completion must wait for downstream work.
                received.append((role, text))

            with (
                patch.dict(os.environ, {"MEETING_REAZON_RUNTIME": "rust", "MEETING_REAZON_WORKER": str(executable)}),
                patch("app.stt.native_pipeline.cached_reazonspeech_snapshot", return_value=directory),
            ):
                pipeline = build_pipeline(frames, "other", config(), broadcast, handle)
                assert isinstance(pipeline, NativeSttPipeline)
                ready = asyncio.Event()

                async def on_ready() -> None:
                    ready.set()

                pipeline.on_ready = on_ready
                pipeline.initialize(asyncio.get_running_loop())
                _ = await asyncio.wait_for(ready.wait(), 3)
                try:
                    for _ in range(2):
                        pipeline.start(asyncio.get_running_loop())
                        frames.put_nowait(AudioFrame(bytes(960), False, 0))
                        await pipeline.stop_and_drain()
                    self.assertEqual(received, [("other", "テストです。"), ("other", "テストです。")])
                    await pipeline.stop_and_drain()
                    self.assertEqual(len(received), 2)
                finally:
                    await pipeline.shutdown_and_wait()

    async def test_malformed_or_wrong_generation_never_reaches_conversation(self) -> None:
        for replacement in ('response["id"] = 999', 'response["segment"]["generation"] = 999'):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as directory:
                executable = Path(directory) / "worker"
                source = _WORKER.replace(
                    "    print(json.dumps(response), flush=True)",
                    f'    if op == "finish":\n        {replacement}\n    print(json.dumps(response), flush=True)',
                )
                _ = executable.write_text(f"#!{sys.executable}\n" + source)
                executable.chmod(0o700)
                worker = NativeWorker(executable, Path(directory), None)
                await worker.open()
                try:
                    _ = await worker.request({"op": "audio", "pcm": [0]}, "audio")
                    if "id" in replacement:
                        with self.assertRaises(NativeWorkerError):
                            _ = await worker.request({"op": "finish"}, "finished")
                    else:
                        response = await worker.request({"op": "finish"}, "finished")
                        received: list[str] = []

                        async def broadcast(_message: OutgoingMessage) -> None:
                            pass

                        async def handle(_role: str, text: str) -> None:
                            received.append(text)

                        pipeline = NativeSttPipeline(queue.Queue(), config(), "other", broadcast, handle)
                        with self.assertRaises(NativeWorkerError):
                            await pipeline._accept(response)  # pyright: ignore[reportPrivateUsage]
                        self.assertEqual(received, [])
                finally:
                    await worker.close()

    async def test_hung_and_crashed_worker_are_reaped(self) -> None:
        for behavior in ("time.sleep(60)", "sys.exit(2)", 'print("not-json", flush=True)'):
            with self.subTest(behavior=behavior), tempfile.TemporaryDirectory() as directory:
                executable = Path(directory) / "worker"
                _ = executable.write_text(
                    f"#!{sys.executable}\nimport json, sys, time\n"
                    + 'print(json.dumps(dict(id=None, type="ready", protocol=2, '
                    + "transcription_available=True)), flush=True)\n"
                    + f"sys.stdin.readline()\n{behavior}\n"
                )
                executable.chmod(0o700)
                worker = NativeWorker(executable, Path(directory), None)
                await worker.open()
                try:
                    with self.assertRaises(NativeWorkerError):
                        _ = await worker.request({"op": "prepare"}, "prepared", timeout=0.1)
                finally:
                    await asyncio.wait_for(worker.close(), 2)

    async def test_existing_meeting_stop_persists_both_final_transcripts(self) -> None:
        from uuid import uuid4

        from app.core.protocols import AudioPipelineLike
        from app.meetings.lifecycle import MeetingLifecycleCoordinator
        from app.meetings.models import Turn
        from app.meetings.service import MeetingHistoryService
        from app.meetings.sqlite_repository import SqliteMeetingHistoryRepository
        from app.services.conversation_orchestrator import ConversationOrchestrator
        from app.services.stt_controller import SttController
        from tests.app.meetings.test_lifecycle import FakeConversationState, FakeWs
        from tests.app.services.test_stt_controller_lifecycle import FakeAudioPipeline

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "worker"
            _ = executable.write_text(f"#!{sys.executable}\n" + _WORKER)
            executable.chmod(0o700)
            state = FakeConversationState()
            messages: list[OutgoingMessage] = []
            audio: list[FakeAudioPipeline] = []

            async def broadcast(message: OutgoingMessage) -> None:
                messages.append(message)

            def make_audio(_device: int | str | None, role: str) -> FakeAudioPipeline:
                source = FakeAudioPipeline(role)
                audio.append(source)
                return source

            def make_turn(*, speaker: str, text: str, speaker_id: str | None = None) -> Turn:
                return Turn(id=str(uuid4()), speaker=speaker, text=text, speaker_id=speaker_id)

            repository = SqliteMeetingHistoryRepository(Path(directory) / "history.sqlite3")
            await repository.initialize()
            history = MeetingHistoryService(repository)
            conversation = ConversationOrchestrator(
                state,
                broadcast,
                [],
                None,
                make_turn,
                info_enabled=False,
                history_service=history,
            )

            def make_stt(source: AudioPipelineLike, role: str) -> NativeSttPipeline:
                return NativeSttPipeline(source.stt_queue, config(), role, broadcast, conversation.handle_speech)

            controller = SttController(state, "reazonspeech", make_audio, make_stt, lambda: [], broadcast)
            lifecycle = MeetingLifecycleCoordinator(
                state,
                controller,
                broadcast,
                history,
                conversation.cancel_replies,
                conversation.clear_reply_cancel_results,
                conversation.reset_info_note_updater,
            )
            with (
                patch.dict(os.environ, {"MEETING_REAZON_WORKER": str(executable)}),
                patch(
                    "app.stt.native_pipeline.cached_reazonspeech_snapshot",
                    return_value=directory,
                ),
            ):
                try:
                    await controller.start_level_monitors()
                    await controller.init_stt()
                    async with asyncio.timeout(3):
                        while not state.stt_initialized:
                            await asyncio.sleep(0.01)
                    await lifecycle.start_meeting(FakeWs())
                    state._is_running = True  # pyright: ignore[reportPrivateUsage]
                    for source in audio:
                        source.stt_queue.put_nowait(AudioFrame(bytes(960), False, 0))
                    await lifecycle.stop_meeting()
                    meetings = await repository.list_meetings()
                    self.assertEqual(len(meetings), 1)
                    detail = await history.get_meeting_detail(meetings[0].id)
                    assert detail is not None
                    meeting, turns, _suggestions, _assets = detail
                    assert meeting is not None
                    self.assertIsNotNone(meeting.ended_at)
                    self.assertEqual({turn.speaker for turn in turns}, {"self", "other"})
                    self.assertEqual([turn.text for turn in turns], ["テストです。", "テストです。"])
                    self.assertIsNone(state.current_session)
                finally:
                    await controller.shutdown_stt()
                    controller.stop_level_monitors()
                    await repository.close()

    async def test_model_status_uses_selected_native_directory_without_downloading_elsewhere(self) -> None:
        from app.services.reazonspeech_model_manager import ReazonSpeechModelManager
        from app.stt.reazonspeech_model import REAZONSPEECH_MODEL_FILES

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"MEETING_REAZON_RUNTIME": "rust", "MEETING_REAZON_MODEL": directory}),
            patch("app.services.reazonspeech_model_manager.download_reazonspeech_snapshot") as download,
        ):
            manager = ReazonSpeechModelManager()
            self.assertEqual(manager.status().state, "missing")
            self.assertEqual((await manager.start()).state, "failed")
            download.assert_not_called()
            for name in REAZONSPEECH_MODEL_FILES.values():
                (Path(directory) / name).touch()
            status = manager.status()
            self.assertEqual(status.state, "ready")
            self.assertEqual(status.model_path, directory)
            self.assertEqual(status.storage_path, directory)

    async def test_capture_sequence_gap_retires_partial_recognition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "worker"
            _ = executable.write_text(f"#!{sys.executable}\n" + _WORKER)
            executable.chmod(0o700)
            received: list[str] = []
            messages: list[OutgoingMessage] = []

            async def broadcast(message: OutgoingMessage) -> None:
                messages.append(message)

            async def handle(_role: str, text: str) -> None:
                received.append(text)

            frames: queue.Queue[AudioFrame | None] = queue.Queue(maxsize=10)
            with (
                patch.dict(os.environ, {"MEETING_REAZON_WORKER": str(executable)}),
                patch("app.stt.native_pipeline.cached_reazonspeech_snapshot", return_value=directory),
            ):
                pipeline = NativeSttPipeline(frames, config(), "other", broadcast, handle)
                ready = asyncio.Event()

                async def on_ready() -> None:
                    ready.set()

                pipeline.on_ready = on_ready
                pipeline.initialize(asyncio.get_running_loop())
                _ = await asyncio.wait_for(ready.wait(), 3)
                try:
                    pipeline.start(asyncio.get_running_loop())
                    frames.put_nowait(AudioFrame(bytes(960), False, 0, sequence=40))
                    frames.put_nowait(AudioFrame(bytes(960), False, 30, sequence=42))
                    with self.assertRaises(NativeWorkerError):
                        await pipeline.stop_and_drain()
                    self.assertEqual(received, [])
                    self.assertTrue(any(m.type == "error" and "欠落" in m.text for m in messages))
                finally:
                    await pipeline.shutdown_and_wait()
