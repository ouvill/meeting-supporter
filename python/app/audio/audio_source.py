from __future__ import annotations

from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Protocol

import numpy as np

_CAPTURE_CADENCE_MS = 30


if TYPE_CHECKING:
    from soundcard import Microphone, Speaker


class AudioReader(Protocol):
    """Yields float32 mono frames on each read() call."""

    def read(self, numframes: int) -> np.ndarray: ...


class AudioSource(Protocol):
    """Device abstraction; open() provides an AudioReader context manager."""

    name: str
    sample_rate: int

    def open(self) -> AbstractContextManager[AudioReader]: ...


class _Recorder(Protocol):
    def record(self, numframes: int) -> np.ndarray: ...


class _SoundcardModule(Protocol):
    def all_microphones(self, *, include_loopback: bool = ...) -> list[Microphone]: ...
    def default_microphone(self) -> Microphone: ...
    def default_speaker(self) -> Speaker | None: ...
    def get_microphone(self, device: int | str, *, include_loopback: bool = ...) -> Microphone: ...


class SoundcardSource:
    """soundcard-backed AudioSource. Resolves the device at construction time."""

    def __init__(self, device: int | str | None, role: str, samplerate: int) -> None:
        import soundcard as sc

        if device is not None:
            mic = sc.get_microphone(device, include_loopback=True)
        elif role == "other":
            try:
                mic = _resolve_default_speaker_loopback(sc)
            except Exception:
                mic = sc.default_microphone()
        else:
            mic = sc.default_microphone()

        self._mic: Microphone = mic
        self.sample_rate: int = int(samplerate)
        self.device_id: int | str = mic.id
        self.name: str = mic.name

    @contextmanager
    def open(self) -> Generator[AudioReader, None, None]:
        blocksize = max(1, self.sample_rate * _CAPTURE_CADENCE_MS // 1000)
        with self._mic.recorder(samplerate=self.sample_rate, channels=1, blocksize=blocksize) as rec:
            yield _SoundcardReader(rec)


class _SoundcardReader:
    def __init__(self, rec: _Recorder) -> None:
        self._rec: _Recorder = rec

    def read(self, numframes: int) -> np.ndarray:
        data: np.ndarray = self._rec.record(numframes=numframes)
        mono = data.mean(axis=1) if data.ndim > 1 else data.reshape(-1)
        return mono.astype(np.float32)


def _resolve_default_speaker_loopback(sc: _SoundcardModule) -> Microphone:
    speaker: Speaker | None = sc.default_speaker()
    if speaker is None:
        raise RuntimeError("default speaker not found")

    raw_id: object = getattr(speaker, "id", "")
    speaker_id = str(raw_id)
    if not speaker_id:
        raise RuntimeError("default speaker has no id")

    candidates = [speaker_id]
    if not speaker_id.endswith(".monitor"):
        candidates.append(f"{speaker_id}.monitor")

    for candidate in candidates:
        try:
            return sc.get_microphone(candidate, include_loopback=True)
        except Exception:
            continue

    for mic in sc.all_microphones(include_loopback=True):
        mic_id = str(getattr(mic, "id", ""))
        if ".monitor" in mic_id and speaker_id in mic_id:
            return mic

    raise RuntimeError(f"loopback monitor not found for default speaker: {speaker_id}")


__all__ = ["AudioReader", "AudioSource", "SoundcardSource"]
