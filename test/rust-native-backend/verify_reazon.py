"""Real-model parity using locally synthesized Japanese; never use meeting audio.

Python is a test/reference dependency only. The Rust child has an empty PATH.
The developer supplies an Open JTalk voice and dictionary; no automatic downloads.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
from verify import HERE, Client  # Also installs the isolated reference package loader.

TEXT = "これは音声認識の動作確認です。明日の会議は午前十時に始まります。"


def synthesize(
    directory: Path, voice: Path, dictionary: Path
) -> tuple[Path, np.ndarray]:
    executable = shutil.which("open_jtalk")
    if executable is None:
        raise RuntimeError("Install Open JTalk for this synthetic integration check")
    raw = directory / "synthetic-48k.wav"
    subprocess.run(
        [executable, "-x", str(dictionary), "-m", str(voice), "-ow", str(raw)],
        input=TEXT.encode("utf-8"),
        check=True,
        timeout=30,
        env={"PATH": "", "LANG": "C.UTF-8"},
        stderr=subprocess.DEVNULL,
    )
    with wave.open(str(raw)) as reader:
        assert reader.getframerate() == 48_000, "Use a 48 kHz Open JTalk voice"
        assert reader.getnchannels() == 1 and reader.getsampwidth() == 2
        pcm = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2")
    # A three-sample averaging filter then decimation, for this synthetic fixture only.
    pcm = pcm[: len(pcm) // 3 * 3].reshape(-1, 3).mean(axis=1).astype("<i2")
    path = directory / "synthetic.wav"
    with wave.open(str(path), "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        writer.writeframes(pcm.tobytes())
    return path, np.pad(pcm, (0, -len(pcm) % 480))


def verify(
    binary: Path, model: Path, voice: Path, dictionary: Path
) -> dict[str, object]:
    # Import after verify has installed the isolated app package loader.
    from app.stt.reazonspeech_model import (
        load_reazonspeech_recognizer,
        transcribe_reazonspeech,
    )

    with tempfile.TemporaryDirectory(prefix="meeting-synthetic-") as temporary:
        directory = Path(temporary)
        wav, pcm = synthesize(directory, voice, dictionary)
        client = Client(binary, None, model)
        try:
            ready = client.receive()
            ready_ms = (time.perf_counter_ns() - client.started) / 1e6
            assert ready["protocol"] == 2 and ready["transcription_available"] is True
            assert (
                client.call("audio", role="self", pcm=[0] * 480)["code"]
                == "model_not_prepared"
            )
            prepare_id = client.send({"op": "prepare"})
            health_id = client.send({"op": "health"})
            responses = {
                reply["id"]: reply for reply in [client.receive(), client.receive()]
            }
            assert responses[health_id]["type"] == "health"
            assert responses[prepare_id]["type"] == "prepared"
            load_ms = responses[prepare_id]["load_ms"]
            assert client.call("prepare")["load_ms"] == 0.0
            assert (
                client.call("audio", role="self", pcm=[0])["code"]
                == "invalid_frame_length"
            )

            results: list[dict[str, object]] = []
            for frame in pcm.reshape(-1, 480):
                reply = client.call("audio", role="self", pcm=frame.tolist())
                assert reply["type"] == "audio", reply
                if reply["segment"] is not None:
                    results.append(reply["segment"])
                other = client.call("audio", role="other", pcm=[0] * 480)
                assert other["type"] == "audio" and other["segment"] is None
            final = client.call("finish", role="self")
            if final["segment"] is not None:
                results.append(final["segment"])
            assert client.call("finish", role="self")["segment"] is None
            assert (
                client.call("audio", role="self", pcm=[0] * 480)["code"]
                == "source_finished"
            )
            assert client.call("health")["models"] == "ready"
            assert client.call("finish", role="other")["segment"] is None

            reference = load_reazonspeech_recognizer(str(model))
            texts = []
            for segment in results:
                assert segment["generation"] == 0
                start, end = segment["start_sample"], segment["end_sample"]
                assert 0 <= start < end <= len(pcm)
                assert end - start == segment["samples"]
                if segment["accepted"]:
                    audio = pcm[start:end].astype(np.float32) / 32768.0
                    expected = transcribe_reazonspeech(reference, audio)
                    assert segment["recognition"] == {
                        "status": "recognized",
                        "text": expected,
                    }
                    texts.append(expected)
                else:
                    assert segment["recognition"]["status"] == "rejected"
            assert any("音声認識" in text for text in texts), (
                "Must recognize actual synthetic speech"
            )
            assert any("会議" in text for text in texts)

            # Reset discards unflushed speech, clears VAD and advances source generation.
            assert client.call("reset", role="self")["type"] == "reset"
            for frame in pcm[: 480 * 30].reshape(-1, 480):
                client.call("audio", role="self", pcm=frame.tolist())
            client.call("reset", role="self")
            assert client.call("finish", role="self")["segment"] is None
            client.call("reset", role="self")
            restarted = []
            for frame in pcm.reshape(-1, 480):
                segment = client.call("audio", role="self", pcm=frame.tolist())[
                    "segment"
                ]
                if segment is not None:
                    restarted.append(segment)
            segment = client.call("finish", role="self")["segment"]
            if segment is not None:
                restarted.append(segment)
            assert restarted == [dict(segment, generation=3) for segment in results]
            assert client.call("shutdown")["type"] == "stopped"
            assert client.process.wait(timeout=10) == 0
        finally:
            client.close()

        cli = subprocess.run(
            [str(binary), "--reazon-model", str(model), "--wav", str(wav)],
            capture_output=True,
            timeout=60,
            env={"PATH": "", "LANG": "C"},
            check=True,
        )
        assert [json.loads(line) for line in cli.stdout.splitlines()] == results
        with wave.open(str(directory / "invalid.wav"), "wb") as writer:
            writer.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            writer.writeframes(bytes(400))
        invalid = subprocess.run(
            [
                str(binary),
                "--reazon-model",
                str(model),
                "--wav",
                str(directory / "invalid.wav"),
            ],
            capture_output=True,
            timeout=10,
            env={"PATH": "", "LANG": "C"},
            check=False,
        )
        assert invalid.returncode != 0 and not invalid.stdout

        missing = Client(binary, None, directory / "missing-model")
        try:
            assert missing.receive()["type"] == "ready"
            for _ in range(2):
                assert missing.call("prepare")["code"] == "model_files_missing"
                assert missing.call("health")["models"] == "failed"
            assert missing.call("shutdown")["type"] == "stopped"
        finally:
            missing.close()

        overloaded = Client(binary, None, model)
        try:
            overloaded.receive()
            ids = {overloaded.send({"op": "prepare"})}
            for _ in range(64):
                ids.add(
                    overloaded.send({"op": "audio", "role": "self", "pcm": [0] * 480})
                )
            replies = [overloaded.receive() for _ in ids]
            assert {reply["id"] for reply in replies} == ids
            assert any(reply.get("code") == "busy" for reply in replies)
            assert all(
                reply["type"] in {"prepared", "audio", "error"} for reply in replies
            )
            assert all(
                reply["code"] == "busy" for reply in replies if reply["type"] == "error"
            )
            assert overloaded.call("reset", role="self")["type"] == "reset"
            assert overloaded.call("shutdown")["type"] == "stopped"
        finally:
            overloaded.close()
        return {
            "ready_ms": ready_ms,
            "model_load_ms": load_ms,
            "recognized_segments": len(texts),
            "python_parity": True,
            "wav_and_pcm_parity": True,
            "source_isolation_and_reset": True,
            "bounded_queue": True,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary", type=Path, default=HERE / "target/release/meeting-native-backend"
    )
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--voice", type=Path, required=True)
    parser.add_argument(
        "--dictionary",
        type=Path,
        default=Path("/var/lib/mecab/dic/open-jtalk/naist-jdic"),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            verify(
                args.binary.resolve(),
                args.model.resolve(),
                args.voice.resolve(),
                args.dictionary.resolve(),
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
