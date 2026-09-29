"""Synthetic ONNX parity checks and native process-start measurements.

Python is only the test driver/reference. The child receives no Python PATH or
application credentials, and loads ONNX Runtime directly as a native library.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import queue
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "rust-audio-core"))
# Reuse the reference package loader, which skips eager device/provider imports.
import compare as reference
from app.stt.stages.vad import SileroVadEngine


class Client:
    def __init__(
        self, binary: Path, library: Path | None, model: Path | None = None
    ) -> None:
        environment = {"PATH": "", "LANG": "C"}
        if "SYSTEMROOT" in os.environ:
            environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        self.started = time.perf_counter_ns()
        arguments = [str(binary)]
        if library is not None:
            arguments += ["--ort-library", str(library)]
        if model is not None:
            arguments += ["--reazon-model", str(model)]
        self.process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.incoming: queue.Queue[str | None] = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.next_id = 0

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.incoming.put(line)
        self.incoming.put(None)

    def receive(self) -> dict[str, object]:
        line = self.incoming.get(timeout=15)
        if line is None:
            raise AssertionError("native backend exited before response")
        value = json.loads(line)
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise TypeError("invalid response envelope")
        return value

    def send(self, command: dict[str, object]) -> int:
        self.next_id += 1
        assert self.process.stdin is not None
        self.process.stdin.write(
            json.dumps({"id": self.next_id, "command": command}) + "\n"
        )
        self.process.stdin.flush()
        return self.next_id

    def call(self, op: str, **fields: object) -> dict[str, object]:
        request_id = self.send({"op": op, **fields})
        response = self.receive()
        assert response["id"] == request_id
        return response

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)
        self.reader.join(timeout=10)
        if self.process.stdin is not None:
            self.process.stdin.close()
        if self.process.stdout is not None:
            self.process.stdout.close()


def startup(binary: Path, library: Path, prepare: bool) -> tuple[float, float | None]:
    client = Client(binary, library)
    try:
        ready = client.receive()
        ready_ms = (time.perf_counter_ns() - client.started) / 1e6
        assert ready == {
            "id": None,
            "type": "ready",
            "protocol": 2,
            "models": "unloaded",
            "transcription_available": False,
        }
        load_ms = None
        if prepare:
            response = client.call("prepare")
            assert response["type"] == "prepared", response
            load_ms = float(response["load_ms"])
        assert client.call("shutdown")["type"] == "stopped"
        assert client.process.wait(timeout=10) == 0
        return ready_ms, load_ms
    finally:
        client.close()


def reference_frame(
    engine: SileroVadEngine, pcm: np.ndarray
) -> tuple[bool, list[float]]:
    observed: list[float] = []
    infer = engine._infer_probability

    def capture(samples: np.ndarray) -> float:
        probability = infer(samples)
        observed.append(probability)
        return probability

    with patch.object(engine, "_infer_probability", new=capture):
        speech = engine.is_speech(pcm.astype("<i2").tobytes(), 16000)
    return speech, observed


def synthetic_vowel(samples: int) -> np.ndarray:
    """Deterministic harmonic/formant signal; no recorded voice or model download."""
    time_axis = np.arange(samples) / 16000
    phase = 2 * np.pi * (120 * time_axis + 3 * np.sin(2 * np.pi * 2 * time_axis))
    waveform = sum(
        np.sin(harmonic * phase)
        * (
            np.exp(-(((harmonic * 120 - 700) / 200) ** 2))
            + 0.7 * np.exp(-(((harmonic * 120 - 1200) / 250) ** 2))
            + 0.4 * np.exp(-(((harmonic * 120 - 2500) / 300) ** 2))
        )
        / harmonic
        for harmonic in range(1, 31)
    )
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * time_axis)
    return (waveform / np.max(np.abs(waveform)) * 20000 * envelope).astype(np.int16)


def verify(binary: Path, library: Path) -> dict[str, object]:
    # Missing runtime must not prevent UI/control startup, and failure is recoverable.
    missing = library.parent / "missing-test-runtime"
    client = Client(binary, missing)
    try:
        assert client.receive()["type"] == "ready"
        assert client.call("health")["models"] == "unloaded"
        assert client.call("prepare")["code"] == "runtime_unavailable"
        assert client.call("health")["models"] == "failed"
        assert client.call("prepare")["code"] == "runtime_unavailable"
        assert client.call("shutdown")["type"] == "stopped"
    finally:
        client.close()

    client = Client(binary, library)
    checked = 0
    max_difference = 0.0
    accepted_count = 0
    try:
        assert client.receive()["type"] == "ready"
        assert (
            client.call("audio", role="user", pcm=[0] * 480)["code"]
            == "model_not_prepared"
        )
        prepare_id = client.send({"op": "prepare"})
        health_id = client.send({"op": "health"})
        replies = [client.receive(), client.receive()]
        assert {reply["id"] for reply in replies} == {prepare_id, health_id}
        assert {reply["type"] for reply in replies} == {"prepared", "health"}
        assert client.call("health")["models"] == "ready"
        assert client.call("prepare")["load_ms"] == 0.0
        for op, fields, code in [
            ("audio", {"role": "user", "pcm": [0]}, "invalid_frame_length"),
            ("audio", {"role": "user", "pcm": [32768]}, "invalid_request"),
            ("health", {"unexpected": True}, "invalid_request"),
        ]:
            client.send({"op": op, **fields})
            assert client.receive()["code"] == code
        assert client.call("health")["models"] == "ready"

        # Distinct, interleaved streams check that recurrent state is not shared.
        engines = {role: SileroVadEngine(0.5) for role in ("user", "other")}
        rng = np.random.default_rng(1234)
        vowel = synthetic_vowel(180 * 480)
        inputs: dict[str, list[reference.AudioFrame]] = {role: [] for role in engines}
        actual_segments: dict[str, list[tuple[int, dict[str, object]]]] = {
            role: [] for role in engines
        }
        for frame_index in range(180):
            for role, engine in engines.items():
                if role == "user" and frame_index % 60 < 45:
                    pcm = vowel[frame_index * 480 : (frame_index + 1) * 480]
                elif role == "other" and frame_index % 40 < 30:
                    pcm = rng.integers(-16000, 16000, 480, dtype=np.int16)
                else:
                    pcm = np.zeros(480, dtype=np.int16)
                speech, observed = reference_frame(engine, pcm)
                result = client.call("audio", role=role, pcm=pcm.tolist())
                assert result["type"] == "audio"
                assert result["speech"] == speech
                np.testing.assert_allclose(
                    result["probabilities"], observed, atol=1e-5, rtol=1e-5
                )
                if observed:
                    difference = np.abs(
                        np.asarray(result["probabilities"]) - observed
                    ).max()
                    max_difference = max(max_difference, float(difference))
                inputs[role].append(
                    reference.AudioFrame(pcm.tobytes(), speech, frame_index * 30.0)
                )
                if result["segment"] is not None:
                    actual_segments[role].append((frame_index, result["segment"]))
                checked += 1
        for role in engines:
            result = client.call("finish", role=role)
            if result["segment"] is not None:
                actual_segments[role].append((len(inputs[role]), result["segment"]))
            expected, _ = reference.python_replay(inputs[role], reference.CONFIG)
            accepted = [
                (index, segment)
                for index, segment in actual_segments[role]
                if segment["accepted"]
            ]
            assert len(expected) == len(accepted)
            accepted_count += len(accepted)
            for (index, audio), (actual_index, segment) in zip(
                expected, accepted, strict=True
            ):
                assert index == actual_index
                assert len(audio) == segment["samples"]

        assert accepted_count > 0, (
            "synthetic input must exercise the accepted-segment path"
        )
        # Reset clears the partial 512-sample window as well as recurrent state.
        assert client.call("reset", role="user")["type"] == "reset"
        assert client.call("audio", role="user", pcm=[0] * 480)["probabilities"] == []
        result = client.call("audio", role="user", pcm=[0] * 480)
        fresh = SileroVadEngine(0.5)
        fresh.is_speech(bytes(960), 16000)
        assert result["speech"] == fresh.is_speech(bytes(960), 16000)
        assert client.call("shutdown")["type"] == "stopped"
        assert client.process.wait(timeout=10) == 0
    finally:
        client.close()

    # The native parser bounds memory even for a record without a newline.
    result = subprocess.run(
        [str(binary), "--ort-library", str(missing)],
        input=b"x" * (16 * 1024 + 1),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    return {
        "vad_frames": checked,
        "accepted_segments": accepted_count,
        "max_probability_difference": max_difference,
        "checks": [
            "missing_runtime",
            "retry",
            "health_during_prepare",
            "unprepared_audio",
            "invalid_input",
            "two_speakers",
            "segment_handoff",
            "reset",
            "shutdown",
            "bounded_input",
        ],
    }


def find_library() -> Path:
    spec = importlib.util.find_spec("onnxruntime")
    if spec is None or spec.origin is None:
        raise RuntimeError("install onnxruntime==1.24.4 for the reference comparison")
    capi = Path(spec.origin).parent / "capi"
    candidates = [
        *capi.glob("libonnxruntime.so.*"),
        *capi.glob("onnxruntime.dll"),
        *capi.glob("libonnxruntime*.dylib"),
    ]
    if len(candidates) != 1:
        raise RuntimeError("specify --ort-library explicitly")
    return candidates[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary", type=Path, default=HERE / "target/release/meeting-native-backend"
    )
    parser.add_argument("--ort-library", type=Path)
    parser.add_argument("--rounds", type=int, default=15)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    binary = args.binary.resolve()
    library = (args.ort_library or find_library()).resolve()
    results = verify(binary, library)
    startup(
        binary, library, True
    )  # Warm OS caches; these are fresh-process, not cold-disk timings.
    timings = [startup(binary, library, True) for _ in range(args.rounds)]
    ready = [value[0] for value in timings]
    loaded = [value[1] for value in timings if value[1] is not None]
    results.update(
        {
            "platform": f"{platform.system()} {platform.machine()}",
            "python_reference": platform.python_version(),
            "numpy": np.__version__,
            "rounds": args.rounds,
            "process_ready_median_ms": statistics.median(ready),
            "process_ready_max_ms": max(ready),
            "onnx_load_median_ms": statistics.median(loaded),
            "onnx_load_max_ms": max(loaded),
            "binary_bytes": binary.stat().st_size,
        }
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
