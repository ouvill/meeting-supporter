"""Exercise real CPAL capture using only a private Pulse server and synthetic tones."""

import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import select
import struct
import subprocess
import tempfile
import time
import wave


def exact(pipe, count: int, deadline: float) -> bytes:
    result = bytearray()
    while len(result) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([pipe], [], [], remaining)[0]:
            raise AssertionError("audio worker response timed out")
        block = os.read(pipe.fileno(), count - len(result))
        if not block:
            raise AssertionError("audio worker closed its output")
        result.extend(block)
    return bytes(result)


def packet(pipe, deadline: float) -> tuple[dict, bytes]:
    header_length, pcm_length = struct.unpack("<II", exact(pipe, 8, deadline))
    assert 0 < header_length <= 16384 and pcm_length in (0, 960)
    header = json.loads(exact(pipe, header_length, deadline))
    assert isinstance(header, dict) and isinstance(header.get("type"), str)
    assert header["type"] not in ("error", "recording_error"), "capture failed"
    return header, exact(pipe, pcm_length, deadline)


def event(worker, kind: str) -> dict:
    deadline = time.monotonic() + 10
    while True:
        header, _ = packet(worker.stdout, deadline)
        if header["type"] == kind:
            return header


def command(worker, identifier: int, operation: str, **fields) -> None:
    request = {"id": identifier, "command": {"op": operation, **fields}}
    worker.stdin.write((json.dumps(request) + "\n").encode())
    worker.stdin.flush()


@contextlib.contextmanager
def process(arguments, env, **kwargs):
    child = subprocess.Popen(arguments, env=env, stderr=subprocess.DEVNULL, **kwargs)
    try:
        yield child
    finally:
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
        for pipe in (child.stdin, child.stdout):
            if pipe is not None:
                pipe.close()


def tone(path: Path, rate: int) -> None:
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(
            b"".join(
                struct.pack("<hh", value, value)
                for i in range(rate * 2)
                for value in [round(12000 * math.sin(2 * math.pi * 1000 * i / rate))]
            )
        )


def capture(
    worker_path: Path,
    root: Path,
    env: dict,
    role: str,
    sink: str,
    rate: int,
    explicit: str | None = None,
) -> None:
    source = root / f"{role}-tone.wav"
    recorded = root / f"{role}-recorded.wav"
    tone(source, rate)
    arguments = [str(worker_path), "--role", "other" if explicit else role]
    if explicit is not None:
        arguments += ["--device", explicit]
    with process(
        arguments, env, stdin=subprocess.PIPE, stdout=subprocess.PIPE
    ) as worker:
        ready = event(worker, "ready")
        assert ready.get("protocol") == 1 and ready.get("rate") == 16000
        command(worker, 1, "start_recording", path=str(recorded))
        assert event(worker, "recording_started").get("id") == 1
        with process(["paplay", f"--device={sink}", str(source)], env) as player:
            deadline = time.monotonic() + 10
            previous = None
            audible_frames = 0
            while audible_frames < 30:
                header, pcm = packet(worker.stdout, deadline)
                if header["type"] != "audio":
                    continue
                sequence = header.get("sequence")
                assert isinstance(sequence, int)
                if previous is not None:
                    assert sequence == previous + 1, "unexpected audio transport gap"
                previous = sequence
                samples = struct.unpack("<480h", pcm)
                peak = max(abs(sample) for sample in samples)
                assert abs(header["peak"] - peak / 32768) < 1e-6
                if peak > 5000:
                    audible_frames += 1
            command(worker, 2, "stop_recording")
            stopped = event(worker, "recording_stopped")
            assert stopped.get("id") == 2
            info = stopped.get("recording")
            assert isinstance(info, dict) and info.get("samples", 0) >= 30 * 480
            command(worker, 3, "shutdown")
            assert event(worker, "stopped").get("id") == 3
            assert worker.wait(timeout=5) == 0
            # Close playback early only after successful capture and finalization.
            player.terminate()
        with wave.open(str(recorded), "rb") as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
                1,
                2,
                16000,
            )
            assert wav.getnframes() == info["samples"]
            samples = struct.unpack(
                f"<{wav.getnframes()}h", wav.readframes(wav.getnframes())
            )
            assert max(abs(sample) for sample in samples) > 5000
        assert recorded.stat().st_size == info["size_bytes"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--pulseaudio", default="pulseaudio")
    parser.add_argument("--modules", type=Path)
    args = parser.parse_args()
    worker = args.worker.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="meeting-cpal-") as temporary:
        root = Path(temporary)
        socket = root / "native"
        cookie = root / "cookie"
        cookie.write_bytes(bytes(256))
        cookie.chmod(0o600)
        # Do not inherit audio endpoints, credentials, or the user's Pulse config.
        env = {
            key: os.environ[key]
            for key in ("PATH", "LD_LIBRARY_PATH")
            if key in os.environ
        }
        env.update(
            {
                "XDG_RUNTIME_DIR": temporary,
                "XDG_CONFIG_HOME": temporary,
                "PULSE_RUNTIME_PATH": temporary,
                "PULSE_STATE_PATH": temporary,
                "PULSE_CONFIG_PATH": temporary,
                "PULSE_COOKIE": str(cookie),
                "PULSE_SERVER": f"unix:{socket}",
            }
        )
        config = root / "server.pa"
        config.write_text(
            f"load-module module-native-protocol-unix socket={socket} auth-cookie={cookie}\n"
            "load-module module-null-sink sink_name=synthetic-self rate=48000 channels=2\n"
            "load-module module-null-sink sink_name=synthetic-other rate=44100 channels=2\n"
            "load-module module-remap-source master=synthetic-self.monitor source_name=synthetic-microphone\n"
            "set-default-source synthetic-microphone\n"
            "set-default-sink synthetic-other\n",
            encoding="utf-8",
        )
        arguments = [
            args.pulseaudio,
            "-n",
            "--daemonize=no",
            "--exit-idle-time=-1",
            "--use-pid-file=no",
            "--disallow-exit",
            f"--file={config}",
        ]
        if args.modules:
            arguments.append(f"--dl-search-path={args.modules}")
        with process(arguments, env, stdout=subprocess.DEVNULL) as server:
            deadline = time.monotonic() + 10
            while True:
                assert server.poll() is None, (
                    "private PulseAudio server failed to start"
                )
                assert time.monotonic() < deadline, (
                    "private PulseAudio server timed out"
                )
                if socket.exists():
                    probe = subprocess.run(
                        ["pactl", "info"], env=env, capture_output=True, timeout=2
                    )
                    if probe.returncode == 0:
                        break
                time.sleep(0.05)
            listed = subprocess.run(
                [str(worker), "--list-devices"],
                env=env,
                capture_output=True,
                timeout=10,
                check=True,
            )
            header_length, pcm_length = struct.unpack("<II", listed.stdout[:8])
            assert pcm_length == 0 and len(listed.stdout) == header_length + 8
            result = json.loads(listed.stdout[8:])
            assert isinstance(result, dict) and result.get("type") == "devices"
            devices = result.get("devices")
            assert isinstance(devices, list) and all(
                isinstance(d, dict) for d in devices
            )
            defaults = {
                d["is_monitor"]: d["index"] for d in devices if d.get("is_default")
            }
            assert defaults == {
                False: "synthetic-microphone",
                True: "synthetic-other.monitor",
            }
            capture(worker, root, env, "self", "synthetic-self", 48000)
            capture(worker, root, env, "other", "synthetic-other", 44100)
            capture(
                worker,
                root,
                env,
                "selected",
                "synthetic-self",
                48000,
                explicit="synthetic-self.monitor",
            )
    print(
        "CPAL capture passed: default microphone, default monitor, explicit device, PCM and WAV."
    )


if __name__ == "__main__":
    main()
