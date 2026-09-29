"""Differential replay against the actual Python stage, using synthetic PCM only."""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import queue
import statistics
import struct
import subprocess
import sys
import time
from collections.abc import Coroutine
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import numpy as np
from numpy.typing import NDArray

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

# These __init__ files eagerly import all audio devices and STT providers.
# Load their package paths without initializing hardware/provider dependencies.
# The reference stage, configuration, messages and base classes are unmodified.
for package in ("app.audio", "app.stt"):
    spec = importlib.util.spec_from_loader(package, loader=None, is_package=True)
    if spec is None:
        raise RuntimeError("cannot create reference package")
    module: ModuleType = importlib.util.module_from_spec(spec)
    module.__path__ = [str(ROOT / "python" / package.replace(".", "/"))]
    sys.modules[package] = module

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import OutgoingMessage
from app.stt.stages.stt_reazonspeech import (
    _MAX_SEGMENT_FRAMES,
    ReazonSpeechEngine,
    ReazonSpeechStage,
    _ReazonSpeechJob,
)

FRAME_SAMPLES = 480
CONFIG = SttConfig(
    backend="reazonspeech",
    whisper_model="",
    deepgram_model="",
    language="ja",
    vad_sensitivity=0.5,
    silence_duration=0.4,
    vad_aggressiveness=2,
    device="cpu",
    remote_url="",
    remote_token="",
    sample_rate=16000,
    chunk_size=480,
)
type Output = list[tuple[int, NDArray[np.float32]]]


class ReplayQueue(queue.Queue[AudioFrame | None]):
    index: int = -1

    def get(
        self, block: bool = True, timeout: float | None = None
    ) -> AudioFrame | None:
        frame = super().get(block, timeout)
        self.index += 1
        return frame


class NullPublisher:
    def publish(self, msg: OutgoingMessage) -> None:
        pass

    def schedule(self, coro: Coroutine[object, object, object]) -> None:
        coro.close()
        raise AssertionError("inference must not run in this replay")


async def no_speech(role: str, text: str) -> None:
    raise AssertionError("inference must not run in this replay")


def python_replay(frames: list[AudioFrame], cfg: SttConfig) -> tuple[Output, float]:
    incoming = ReplayQueue()
    for frame in frames:
        incoming.put(frame)
    incoming.put(None)
    output: Output = []

    def capture(job: _ReazonSpeechJob) -> None:
        output.append((incoming.index, job.audio))

    stage = ReazonSpeechStage(incoming, cfg, "synthetic", NullPublisher(), no_speech)
    with patch.object(ReazonSpeechEngine, "enqueue", new=capture):
        start = time.perf_counter_ns()
        stage._run()
        elapsed_ms = (time.perf_counter_ns() - start) / 1e6
    return output, elapsed_ms


def encode(frames: list[AudioFrame]) -> bytes:
    return b"".join(bytes([frame.is_speech]) + frame.pcm for frame in frames)


def rust_replay(
    binary: Path, payload: bytes, cfg: SttConfig
) -> tuple[Output, float, float]:
    start = time.perf_counter_ns()
    result = subprocess.run(
        [
            str(binary),
            str(cfg.silence_duration),
            str(cfg.min_voiced_ms),
            str(cfg.min_voiced_ratio),
            str(cfg.min_rms_dbfs),
        ],
        input=payload,
        capture_output=True,
        check=True,
        timeout=30,
    )
    wall_ms = (time.perf_counter_ns() - start) / 1e6
    output: Output = []
    position = 0
    while position < len(result.stdout):
        index, count = struct.unpack_from("<QI", result.stdout, position)
        position += 12
        if count > 939 * FRAME_SAMPLES or position + count * 4 > len(result.stdout):
            raise AssertionError("invalid Rust output length")
        samples = np.frombuffer(
            result.stdout, dtype="<f4", count=count, offset=position
        )
        output.append((index, samples))
        position += count * 4
    return output, int(result.stderr) / 1e6, wall_ms


def assert_equal(expected: Output, actual: Output) -> None:
    assert len(expected) == len(actual), (len(expected), len(actual))
    for (left_index, left), (right_index, right) in zip(expected, actual, strict=True):
        assert left_index == right_index, (left_index, right_index)
        np.testing.assert_array_equal(left, right)


def frames_for(runs: list[tuple[int, bool, int]]) -> list[AudioFrame]:
    frames: list[AudioFrame] = []
    for count, speech, amplitude in runs:
        pcm = np.full(FRAME_SAMPLES, amplitude, dtype="<i2").tobytes()
        for _ in range(count):
            frames.append(AudioFrame(pcm, speech, len(frames) * 30.0))
    return frames


def verify(binary: Path) -> int:
    assert _MAX_SEGMENT_FRAMES == 934, (
        "Python model limit changed; review the Rust port"
    )
    cases = [
        [],
        [(100, False, 0)],
        [(7, True, 2000)],
        [(8, True, 2000)],
        [(10, False, 32767), (8, True, 0)],
        [(8, False, 100), (8, True, -32768), (13, False, 0)],
        [(10, True, 2000), (12, False, 0), (10, True, 2000), (13, False, 0)],
        [(934 * 2 + 10, True, 2000)],
        [(8, True, 184)],
        [(8, True, 185)],
    ]
    configs = [
        CONFIG,
        replace(CONFIG, silence_duration=0),
        replace(CONFIG, min_voiced_ratio=1.0),
        replace(CONFIG, min_voiced_ms=0, min_voiced_ratio=0, min_rms_dbfs=-120),
    ]
    checked = 0
    for cfg in configs:
        for runs in cases:
            frames = frames_for(runs)
            expected, _ = python_replay(frames, cfg)
            actual, _, _ = rust_replay(binary, encode(frames), cfg)
            assert_equal(expected, actual)
            checked += 1
    rng = np.random.default_rng(42)
    for _ in range(20):
        frames = frames_for(
            [
                (
                    int(rng.integers(1, 50)),
                    bool(rng.integers(0, 2)),
                    int(rng.integers(-32768, 32768)),
                )
                for _ in range(30)
            ]
        )
        # Also check non-constant PCM, including both signed extremes.
        for frame in frames[::3]:
            frame.pcm = (
                rng.integers(-32768, 32768, FRAME_SAMPLES, dtype=np.int16)
                .astype("<i2")
                .tobytes()
            )
        expected, _ = python_replay(frames, CONFIG)
        actual, _, _ = rust_replay(binary, encode(frames), CONFIG)
        assert_equal(expected, actual)
        checked += 1
    for payload in (b"\x02" + bytes(960), b"\x01", b"\x00" + bytes(959)):
        result = subprocess.run(
            [str(binary)], input=payload, capture_output=True, timeout=10, check=False
        )
        assert result.returncode != 0, "malformed input accepted"
    return checked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary",
        type=Path,
        default=Path(__file__).parent / "target/release/meeting-audio-core",
    )
    parser.add_argument("--rounds", type=int, default=7)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    binary = args.binary.resolve()
    checked = verify(binary)
    workloads = {
        "silence_5min": frames_for([(10000, False, 0)]),
        "turns_5min": frames_for([(70, True, 2000), (30, False, 0)] * 100),
        "continuous_5min": frames_for([(10000, True, 2000)]),
    }
    report: dict[str, object] = {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "platform": f"{platform.system()} {platform.machine()}",
        "parity_cases": checked,
        "malformed_cases": 3,
        "rounds": args.rounds,
    }
    for name, frames in workloads.items():
        payload = encode(frames)
        expected, _ = python_replay(frames, CONFIG)
        actual, _, _ = rust_replay(binary, payload, CONFIG)
        assert_equal(expected, actual)
        python_times, rust_times, wall_times = [], [], []
        for _ in range(args.rounds):
            _, python_ms = python_replay(frames, CONFIG)
            _, rust_ms, wall_ms = rust_replay(binary, payload, CONFIG)
            python_times.append(python_ms)
            rust_times.append(rust_ms)
            wall_times.append(wall_ms)
        report[name] = {
            "frames": len(frames),
            "accepted_segments": len(expected),
            "python_stage_median_ms": statistics.median(python_times),
            "rust_core_median_ms": statistics.median(rust_times),
            "rust_process_with_io_median_ms": statistics.median(wall_times),
        }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
