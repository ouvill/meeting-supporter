# pyright: reportUninitializedInstanceVariable=false
"""Real Rust PCM router with isolated synthetic capture/inference processes."""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import wave
from pathlib import Path
from typing import final, override
from unittest.mock import patch

from app.audio.media_pipeline import MediaAudioPipeline
from app.audio.media_transport import MediaCommandError, MediaError, MediaEvent, MediaProcess
from app.core.messages import OutgoingMessage
from app.stt.media_pipeline import MediaSttPipeline
from tests.app.stt.test_native_pipeline import config

_CAPTURE = r"""
import json, os, struct, sys, threading, time, wave
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
device = args.get("--device", "")
if device == "oversized":
    sys.stdout.buffer.write(struct.pack("<II", 2**31, 0))
    sys.stdout.buffer.flush()
    time.sleep(60)
lock = threading.Lock()
sequence = 0
recording = None
samples = 0
started = 0
path = None
def send(event, pcm=b""):
    header = json.dumps(event).encode()
    sys.stdout.buffer.write(struct.pack("<II", len(header), len(pcm)) + header + pcm)
    sys.stdout.buffer.flush()
def audio():
    global sequence, samples
    while True:
        time.sleep(.01)
        with lock:
            pcm = b"\x00\x10" * 480
            if recording:
                recording.writeframesraw(pcm)
                samples += 480
            send(dict(type="audio", sequence=sequence, peak=.125), pcm)
            sequence += 2 if device == "gap" else 1
with lock:
    send(dict(type="ready", protocol=1, rate=16000, name="Synthetic"))
threading.Thread(target=audio, daemon=True).start()
for line in sys.stdin:
    request = json.loads(line)
    command, identity = request["command"], request["id"]
    with lock:
        if command["op"] == "start_recording":
            path = command["path"]
            recording = wave.open(path, "wb")
            recording.setnchannels(1)
            recording.setsampwidth(2)
            recording.setframerate(16000)
            samples = 0
            started = int(time.time() * 1000)
            send(dict(type="recording_started", id=identity))
        elif command["op"] == "stop_recording":
            info = None
            if recording:
                recording.close()
                recording = None
                info = dict(size_bytes=os.stat(path).st_size, samples=samples,
                            started_ms=started, ended_ms=int(time.time()*1000))
            send(dict(type="recording_stopped", id=identity, recording=info))
        elif command["op"] == "shutdown":
            if recording: recording.close()
            send(dict(type="stopped", id=identity))
            break
"""
_SPEECH = r"""
import json, os, pathlib, sys, time
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
model = pathlib.Path(args["--reazon-model"])
(model / "pid").write_text(str(os.getpid()))
def send(identity, kind, **fields):
    print(json.dumps(dict(id=identity, type=kind, **fields)), flush=True)
send(None, "ready", protocol=2, transcription_available=True)
generation = 0
samples = 0
for line in sys.stdin:
    request = json.loads(line)
    identity, command = request["id"], request["command"]
    op = command["op"]
    if op == "prepare":
        (model / "preparing").touch()
        if model.name == "hang": time.sleep(60)
        send(identity, "prepared")
    elif op == "configure": send(identity, "configured")
    elif op == "reset":
        generation += 1
        samples = 0
        send(identity, "reset")
    elif op == "audio":
        if model.name == "crash": os._exit(23)
        samples += len(command["pcm"])
        send(identity, "audio", segment=None)
    elif op == "finish":
        segment = None
        if samples:
            segment = dict(generation=generation, start_sample=0, end_sample=samples,
                recognition=dict(status="recognized", text="synthetic",
                punctuation=dict(status="applied", text="synthetic。")))
        send(identity, "finished", segment=segment)
"""

_CONFIG: dict[str, object] = {
    "vad_threshold": 0.4,
    "silence_seconds": 0.4,
    "min_voiced_ms": 90,
    "min_voiced_ratio": 0.1,
    "min_rms_dbfs": -60.0,
}


@unittest.skipUnless(os.environ.get("MEETING_MEDIA_WORKER"), "Build and select Rust media runtime")
@final
class MediaRuntimeTest(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.model = self.directory / "model"
        self.model.mkdir()
        for name, source in (("capture", _CAPTURE), ("speech", _SPEECH)):
            executable = self.directory / name
            _ = executable.write_text(f"#!{sys.executable}\n" + textwrap.dedent(source))
            executable.chmod(0o700)
        environment = patch.dict(
            os.environ,
            {
                "MEETING_AUDIO_WORKER": str(self.directory / "capture"),
                "MEETING_REAZON_WORKER": str(self.directory / "speech"),
                "MEETING_REAZON_MODEL": str(self.model),
            },
        )
        _ = self.enterContext(environment)  # pyright: ignore[reportAny] # unittest context helper is untyped.
        self.events: list[MediaEvent] = []
        self.worker = MediaProcess("other", None, self.events.append)
        ready = await asyncio.to_thread(self.worker.receive, None, 5)
        self.assertEqual(ready.protocol, 1)

    @override
    async def asyncTearDown(self) -> None:
        await asyncio.to_thread(self.worker.close)
        self.temporary.cleanup()

    async def prepare(self, name: str = "model") -> None:
        model = self.directory / name
        model.mkdir(exist_ok=True)
        _ = await asyncio.to_thread(
            self.worker.request,
            {
                "op": "prepare",
                "model": str(model),
                "punctuation": None,
                "config": _CONFIG,
            },
        )

    async def test_pcm_stays_in_rust_and_final_segment_precedes_stop_reply(self) -> None:
        await self.prepare()
        path = self.directory / "recording.wav"
        _ = await asyncio.to_thread(self.worker.request, {"op": "start_recording", "path": str(path)})
        _ = await asyncio.to_thread(self.worker.request, {"op": "start_speech"})
        await asyncio.sleep(0.15)
        stopped = await asyncio.to_thread(self.worker.request, {"op": "stop_speech"})
        self.assertEqual(stopped.generation, 1)
        transcripts = [event for event in self.events if event.type == "transcript"]
        self.assertEqual(len(transcripts), 1)
        self.assertEqual(transcripts[0].text, "synthetic。")
        self.assertTrue(all("pcm" not in event.model_dump() for event in self.events))
        result = await asyncio.to_thread(self.worker.request, {"op": "stop_recording"})
        assert result.recording is not None
        self.assertGreater(result.recording.samples, 480)
        self.assertEqual(result.recording.size_bytes, path.stat().st_size)
        _ = await asyncio.to_thread(self.worker.request, {"op": "start_speech"})
        await asyncio.sleep(0.05)
        restarted = await asyncio.to_thread(self.worker.request, {"op": "stop_speech"})
        self.assertEqual(restarted.generation, 2)

    async def test_inference_death_does_not_stop_recording(self) -> None:
        await self.prepare("crash")
        path = self.directory / "recording.wav"
        _ = await asyncio.to_thread(self.worker.request, {"op": "start_recording", "path": str(path)})
        _ = await asyncio.to_thread(self.worker.request, {"op": "start_speech"})
        async with asyncio.timeout(5):
            while not any(event.type == "speech_error" for event in self.events):
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.15)
        with self.assertRaises(MediaCommandError):
            _ = await asyncio.to_thread(self.worker.request, {"op": "stop_speech"})
        result = await asyncio.to_thread(self.worker.request, {"op": "stop_recording"})
        assert result.recording is not None
        self.assertGreater(result.recording.samples, 10 * 480)

    async def test_shutdown_interrupts_preparation_and_reaps_inference(self) -> None:
        model = self.directory / "hang"
        model.mkdir()
        preparing = asyncio.create_task(self.prepare("hang"))
        async with asyncio.timeout(5):
            while not (model / "preparing").exists():
                await asyncio.sleep(0.01)
        started = time.monotonic()
        _ = await asyncio.to_thread(self.worker.request, {"op": "shutdown_speech"})
        with self.assertRaises(MediaCommandError):
            await preparing
        self.assertLess(time.monotonic() - started, 3)
        pid = int((model / "pid").read_text())
        async with asyncio.timeout(3):
            while Path(f"/proc/{pid}").exists():
                await asyncio.sleep(0.01)

    async def test_existing_python_adapter_delivers_only_final_text(self) -> None:
        messages: list[OutgoingMessage] = []
        texts: list[str] = []
        ready = asyncio.Event()

        async def broadcast(message: OutgoingMessage) -> None:
            messages.append(message)

        async def handle_speech(_role: str, text: str) -> None:
            texts.append(text)

        async def on_ready() -> None:
            ready.set()

        audio = MediaAudioPipeline(None, "other", broadcast)
        cfg = config()
        stt = MediaSttPipeline(audio, cfg, "other", broadcast, handle_speech)
        try:
            await asyncio.to_thread(audio.start, asyncio.get_running_loop())
            with patch("app.stt.media_pipeline.cached_reazonspeech_snapshot", return_value=str(self.model)):
                stt.on_ready = on_ready
                stt.initialize(asyncio.get_running_loop())
                async with asyncio.timeout(5):
                    _ = await ready.wait()
            stt.start(asyncio.get_running_loop())
            await asyncio.sleep(0.15)
            await stt.stop_and_drain()
            self.assertEqual(texts, ["synthetic。"])
            self.assertTrue(audio.stt_queue.empty())
            self.assertTrue(audio.recording_queue.empty())
            # Start/stop in the same event-loop turn must retain command order.
            for _ in range(2):
                stt.start(asyncio.get_running_loop())
                await stt.stop_and_drain()
        finally:
            await stt.shutdown_and_wait()
            await asyncio.to_thread(audio.stop)

    async def test_capture_sequence_gap_rejects_inference_without_stopping_capture(self) -> None:
        await asyncio.to_thread(self.worker.close)
        self.worker = MediaProcess("other", "gap", self.events.append)
        _ = await asyncio.to_thread(self.worker.receive, None, 5)
        await self.prepare()
        _ = await asyncio.to_thread(self.worker.request, {"op": "start_speech"})
        async with asyncio.timeout(3):
            while not any(event.type == "speech_error" for event in self.events):
                await asyncio.sleep(0.01)
        with self.assertRaises(MediaCommandError):
            _ = await asyncio.to_thread(self.worker.request, {"op": "stop_speech"})
        self.assertTrue(self.worker.healthy())
        self.assertFalse(any(event.type == "transcript" for event in self.events))

    async def test_oversized_capture_header_fails_before_allocating_payload(self) -> None:
        await asyncio.to_thread(self.worker.close)
        self.worker = MediaProcess("other", "oversized", self.events.append)
        with self.assertRaises(MediaError):
            _ = await asyncio.to_thread(self.worker.receive, None, 3)

    async def test_graceful_shutdown_and_eof_exit(self) -> None:
        await self.prepare()
        _ = await asyncio.to_thread(self.worker.request, {"op": "start_speech"})
        _ = await asyncio.to_thread(self.worker.request, {"op": "shutdown"})
        assert self.worker.process.stdin is not None
        self.worker.process.stdin.close()
        result = await asyncio.to_thread(self.worker.process.wait, 5)
        self.assertEqual(result, 0)

    @unittest.skipUnless(
        os.environ.get("MEETING_SESSION_WORKER") and os.environ.get("MEETING_STORAGE_WORKER"),
        "Select Rust meeting/session storage workers for full integration",
    )
    async def test_existing_meeting_lifecycle_with_two_rust_audio_sources(self) -> None:
        from app.core.protocols import AudioPipelineLike
        from app.meetings.models import MeetingSession, Turn
        from app.meetings.native_lifecycle import NativeMeetingLifecycleCoordinator
        from app.meetings.native_repository import NativeMeetingHistoryRepository
        from app.meetings.recording import RecordingService
        from app.meetings.service import MeetingHistoryService
        from app.services.stt_controller import SttController
        from tests.app.meetings.test_lifecycle import FakeConversationState, FakeWs

        state = FakeConversationState()
        repository = NativeMeetingHistoryRepository(self.directory / "history.sqlite3")
        await repository.initialize()
        history = MeetingHistoryService(repository)
        delivered: list[str] = []

        async def broadcast(_message: OutgoingMessage) -> None:
            pass

        async def speech(role: str, text: str) -> None:
            session = state.current_session
            assert isinstance(session, MeetingSession)
            turn = Turn(id=role + session.id, speaker=role, text=text)
            state.current_session = session.with_turn(turn)
            delivered.append(text)
            _ = history.schedule_insert_turn(session.id, len(delivered), turn)

        def make_audio(device: int | str | None, role: str) -> MediaAudioPipeline:
            return MediaAudioPipeline(device, role, broadcast)

        def make_stt(audio: AudioPipelineLike, role: str) -> MediaSttPipeline:
            assert isinstance(audio, MediaAudioPipeline)
            return MediaSttPipeline(audio, config(), role, broadcast, speech)

        async def reset() -> None:
            pass

        controller = SttController(state, "reazonspeech", make_audio, make_stt, lambda: [], broadcast)
        lifecycle = NativeMeetingLifecycleCoordinator(
            state=state,
            stt_controller=controller,
            broadcast=broadcast,
            history=history,
            cancel_replies=reset,
            reset_reply_cancel_results=lambda: None,
            reset_info_note_updater=reset,
            recording=RecordingService(self.directory),
            user_data_dir=self.directory,
        )
        try:
            await controller.start_level_monitors()
            with patch("app.stt.media_pipeline.cached_reazonspeech_snapshot", return_value=str(self.model)):
                await controller.init_stt()
                async with asyncio.timeout(5):
                    while not state.stt_initialized:
                        await asyncio.sleep(0.01)
            await lifecycle.start_meeting(FakeWs())
            assert isinstance(state.current_session, MeetingSession)
            identity = state.current_session.id
            await asyncio.sleep(0.15)
            await lifecycle.stop_meeting()
            meeting = await repository.get_meeting(identity)
            assert meeting is not None
            self.assertEqual(meeting.status, "completed")
            self.assertEqual(len(await repository.list_turns(identity)), 2)
            assets = await repository.list_recording_assets(identity)
            self.assertEqual(len(assets), 2)
            for asset in assets:
                self.assertEqual((self.directory / asset.relative_path).stat().st_size, asset.size_bytes)
        finally:
            await lifecycle.close()
            await controller.shutdown_stt()
            controller.stop_level_monitors()
            await repository.close()


@unittest.skipUnless(
    os.environ.get("MEETING_TEST_RUST_MEDIA") == "1" and shutil.which("pulseaudio"),
    "Opt-in real media/capture/inference workers and isolated Pulse server",
)
class RealMediaIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_capture_and_models_with_synthetic_tone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            socket = root / "pulse.sock"
            daemon = subprocess.Popen(
                [
                    "pulseaudio",
                    "-n",
                    "--daemonize=no",
                    "--exit-idle-time=-1",
                    "--use-pid-file=no",
                    f"--load=module-native-protocol-unix socket={socket} auth-anonymous=1",
                    "--load=module-null-sink sink_name=synthetic",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            worker: MediaProcess | None = None
            events: list[MediaEvent] = []
            try:
                async with asyncio.timeout(5):
                    while not socket.exists():
                        await asyncio.sleep(0.02)
                with patch.dict(os.environ, {"PULSE_SERVER": f"unix:{socket}"}):
                    worker = MediaProcess("other", "synthetic.monitor", events.append)
                    _ = await asyncio.to_thread(worker.receive, None, 10)
                    _ = await asyncio.to_thread(
                        worker.request,
                        {
                            "op": "prepare",
                            "model": os.environ["MEETING_REAZON_MODEL"],
                            "punctuation": os.environ.get("MEETING_REAZON_PUNCTUATION"),
                            "config": _CONFIG,
                        },
                        135,
                    )
                    recording = root / "recording.wav"
                    _ = await asyncio.to_thread(worker.request, {"op": "start_recording", "path": str(recording)})
                    _ = await asyncio.to_thread(worker.request, {"op": "start_speech"})
                    tone = root / "tone.wav"
                    with wave.open(str(tone), "wb") as output:
                        output.setnchannels(1)
                        output.setsampwidth(2)
                        output.setframerate(16000)
                        output.writeframes((b"\x00\x20" * 8 + b"\x00\xe0" * 8) * 2000)
                    player = await asyncio.create_subprocess_exec("paplay", "--device=synthetic", str(tone))
                    self.assertEqual(await player.wait(), 0)
                    _ = await asyncio.to_thread(worker.request, {"op": "stop_speech"}, 45)
                    result = await asyncio.to_thread(worker.request, {"op": "stop_recording"})
                    assert result.recording is not None
                    self.assertGreater(result.recording.samples, 16000)
                    self.assertEqual(result.recording.size_bytes, recording.stat().st_size)
                    self.assertTrue(any(event.type == "level" and (event.peak or 0) > 0.1 for event in events))
                    self.assertFalse(any(event.type.endswith("error") for event in events))
            finally:
                if worker is not None:
                    await asyncio.to_thread(worker.close)
                daemon.terminate()
                _ = daemon.wait(timeout=5)
