"""Synthetic protocol and opt-in isolated PulseAudio integration tests."""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app.audio.native_pipeline import NativeAudioPipeline, get_native_devices
from app.audio.native_transport import AudioProcess, Event, NativeAudioError
from app.core.messages import OutgoingMessage

_PREAMBLE = """
import json, struct, sys, time
def send(event, pcm=b""):
    header=json.dumps(event).encode()
    sys.stdout.buffer.write(struct.pack("<II",len(header),len(pcm))+header+pcm)
    sys.stdout.buffer.flush()
"""


@unittest.skipUnless(sys.platform == "linux", "Linux audio adapter")
class AudioProtocolTest(unittest.TestCase):
    def test_bad_lengths_and_truncated_frames_fail_without_allocating_payload(self) -> None:
        cases = [
            'sys.stdout.buffer.write(struct.pack("<II", 2**31, 0)); sys.stdout.buffer.flush()',
            'sys.stdout.buffer.write(struct.pack("<II", 10, 960)+b"{}"); sys.stdout.buffer.flush()',
            'send(dict(type="audio",sequence=0,peak=.2))',
            'send(dict(type="audio",sequence=-1,peak=.2),bytes(960))',
        ]
        for source in cases:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                executable = Path(directory) / "worker"
                _ = executable.write_text(f"#!{sys.executable}\n" + _PREAMBLE + source)
                executable.chmod(0o700)
                events: list[Event] = []
                with patch.dict(os.environ, {"MEETING_AUDIO_WORKER": str(executable)}):
                    worker = AudioProcess([], lambda event, _pcm: events.append(event))
                    try:
                        with self.assertRaises(NativeAudioError):
                            _ = worker.receive("ready", timeout=1)
                    finally:
                        worker.close()
                    self.assertIsNotNone(worker.process.returncode)
                    self.assertTrue(all(event.type != "audio" for event in events))

    def test_hung_worker_is_killed_and_reaped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "worker"
            _ = executable.write_text(f"#!{sys.executable}\n" + _PREAMBLE + "time.sleep(60)\n")
            executable.chmod(0o700)
            with patch.dict(os.environ, {"MEETING_AUDIO_WORKER": str(executable)}):
                worker = AudioProcess([], lambda _event, _pcm: None)
                try:
                    with self.assertRaises(NativeAudioError):
                        _ = worker.receive("ready", timeout=0.1)
                finally:
                    worker.close()
                self.assertIsNotNone(worker.process.returncode)


@unittest.skipUnless(
    sys.platform == "linux" and os.environ.get("MEETING_TEST_RUST_AUDIO") == "1" and shutil.which("pulseaudio"),
    "opt-in isolated Pulse server and built Rust audio worker required",
)
class PulseAudioIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_pcm_levels_and_wav_continue_without_stt_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket = Path(directory) / "pulse.sock"
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
            audio: NativeAudioPipeline | None = None
            with patch.dict(os.environ, {"PULSE_SERVER": f"unix:{socket}"}):
                try:
                    async with asyncio.timeout(5):
                        while not socket.exists():
                            await asyncio.sleep(0.02)
                    devices = await asyncio.to_thread(get_native_devices)
                    self.assertTrue(any(d["index"] == "synthetic.monitor" and d["is_monitor"] for d in devices))
                    messages: list[OutgoingMessage] = []

                    async def broadcast(message: OutgoingMessage) -> None:
                        messages.append(message)

                    audio = NativeAudioPipeline("synthetic.monitor", "other", broadcast)
                    await asyncio.to_thread(audio.start, asyncio.get_running_loop())
                    path = Path(directory) / "recording.wav"
                    await asyncio.to_thread(audio.start_recording, path)
                    tone = Path(directory) / "tone.wav"
                    with wave.open(str(tone), "wb") as output:
                        output.setnchannels(1)
                        output.setsampwidth(2)
                        output.setframerate(16000)
                        output.writeframes((b"\x00\x20" * 8 + b"\x00\xe0" * 8) * 7000)
                    player = await asyncio.create_subprocess_exec("paplay", "--device=synthetic", str(tone))
                    self.assertEqual(await player.wait(), 0)
                    await asyncio.sleep(0.2)
                    result = await asyncio.to_thread(audio.stop_recording)
                    assert result is not None
                    with wave.open(str(path), "rb") as recorded:
                        self.assertEqual(recorded.getframerate(), 16000)
                        self.assertGreater(recorded.getnframes(), 7 * 16000)
                    self.assertEqual(result.size_bytes, path.stat().st_size)
                    self.assertTrue(any(m.type == "audio_level" and m.level > 0.1 for m in messages))
                    # The bounded STT queue filled, but Rust's independent WAV did not stop.
                    frame = audio.stt_queue.get_nowait()
                    assert frame is not None and frame.sequence is not None
                    self.assertGreater(frame.sequence, 0)
                    self.assertIsNone(await asyncio.to_thread(audio.stop_recording))
                    await asyncio.to_thread(audio.stop)
                    await asyncio.to_thread(audio.start, asyncio.get_running_loop())
                    audio.ensure_running()
                finally:
                    if audio is not None:
                        await asyncio.to_thread(audio.stop)
                    daemon.terminate()
                    _ = daemon.wait(timeout=5)
