"""CPU INT8 comparison probe; uses only an explicit local model and bounded WAV."""

import argparse
import importlib.metadata
import json
import sys
import time
import wave
from pathlib import Path


def emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--language", choices=("ja", "en"), required=True)
    parser.add_argument("--threads", type=int, choices=range(1, 65), default=4)
    parser.add_argument("--runs", type=int, choices=range(1, 21), default=2)
    args = parser.parse_args()
    if not args.model.is_dir():
        parser.error("--model must be an existing local CTranslate2 model directory")
    with wave.open(str(args.wav), "rb") as reader:
        frames = reader.getnframes()
        if (
            reader.getnchannels() != 1
            or reader.getframerate() != 16000
            or reader.getsampwidth() != 2
            or reader.getcomptype() != "NONE"
            or not 0 < frames <= 30 * 16000
        ):
            parser.error("expected nonempty mono 16 kHz PCM16 WAV, at most 30 seconds")
        pcm = reader.readframes(frames)
        if len(pcm) != frames * 2:
            parser.error("truncated WAV")

    started = time.perf_counter()
    import numpy as np
    from faster_whisper import WhisperModel

    import_ms = (time.perf_counter() - started) * 1000
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    started = time.perf_counter()
    model = WhisperModel(
        str(args.model.resolve()),
        device="cpu",
        compute_type="int8",
        cpu_threads=args.threads,
        num_workers=1,
        local_files_only=True,
    )
    emit(
        {
            "type": "prepared",
            "details": {
                "load_ms": (time.perf_counter() - started) * 1000,
                "import_ms": import_ms,
                "device": model.model.device,
                "compute_type": model.model.compute_type,
                "threads": args.threads,
                "faster_whisper": importlib.metadata.version("faster-whisper"),
                "ctranslate2": importlib.metadata.version("ctranslate2"),
            },
        }
    )
    seconds = len(audio) / 16000
    for run in range(1, args.runs + 1):
        started = time.perf_counter()
        segments, _ = model.transcribe(
            audio,
            language=args.language,
            task="transcribe",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=False,
        )
        # Inference is lazy: consume all segments inside the timed region.
        result = [{"start_ms": round(s.start * 1000), "end_ms": round(s.end * 1000), "text": s.text} for s in segments]
        elapsed = time.perf_counter() - started
        emit(
            {
                "type": "transcript",
                "run": run,
                "result": {
                    "audio_seconds": seconds,
                    "inference_ms": elapsed * 1000,
                    "real_time_factor": elapsed / seconds,
                    "segments": result,
                },
            }
        )
    if sys.platform == "linux":
        import resource

        emit({"type": "memory", "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024})


if __name__ == "__main__":
    main()
