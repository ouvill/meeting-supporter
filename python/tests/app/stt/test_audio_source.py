import sys
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

import numpy as np

from app.audio.audio_source import SoundcardSource


class _FakeRecorder:
    def __init__(self, data: np.ndarray | None = None) -> None:
        self._data: np.ndarray | None = data
        self.numframes: list[int] = []

    def record(self, numframes: int) -> np.ndarray:
        self.numframes.append(numframes)
        if self._data is None:
            return np.zeros(numframes, dtype=np.float32)
        return self._data


class _FakeMicrophone:
    def __init__(self, recorder: _FakeRecorder) -> None:
        self.id: str = "microphone"
        self.name: str = "microphone"
        self._recorder: _FakeRecorder = recorder
        self.recorder_calls: list[tuple[int, int, int | None]] = []

    @contextmanager
    def recorder(
        self,
        *,
        samplerate: int,
        channels: int,
        blocksize: int | None = None,
    ) -> Generator[_FakeRecorder, None, None]:
        self.recorder_calls.append((samplerate, channels, blocksize))
        yield self._recorder


class _FakeSoundcard:
    def __init__(self, microphone: _FakeMicrophone) -> None:
        self._microphone: _FakeMicrophone = microphone

    def get_microphone(self, device: int | str, *, include_loopback: bool = False) -> _FakeMicrophone:
        _ = (device, include_loopback)
        return self._microphone


class TestSoundcardSource(unittest.TestCase):
    def test_role_other_tries_monitor_id_for_default_speaker(self):
        speaker_id = "alsa_output.pci-0000_2f_00.4.iec958-stereo"
        default_speaker = SimpleNamespace(id=speaker_id)
        loopback_mic = SimpleNamespace(
            id=f"{speaker_id}.monitor",
            name="default speaker monitor",
        )

        def get_microphone(device_id: str, include_loopback: bool) -> SimpleNamespace:
            _ = include_loopback
            if device_id.endswith(".monitor"):
                return loopback_mic
            raise IndexError("no soundcard")

        sc = SimpleNamespace(
            default_speaker=mock.Mock(return_value=default_speaker),
            get_microphone=mock.Mock(side_effect=get_microphone),
            all_microphones=mock.Mock(return_value=[]),
            default_microphone=mock.Mock(),
        )

        with mock.patch.dict(sys.modules, {"soundcard": sc}):
            source = SoundcardSource(device=None, role="other", samplerate=16000)

        self.assertEqual(source.name, "default speaker monitor")
        self.assertEqual(source.device_id, f"{speaker_id}.monitor")
        self.assertEqual(
            [call.args[0] for call in sc.get_microphone.call_args_list],  # pyright: ignore[reportAny]
            [speaker_id, f"{speaker_id}.monitor"],
        )

    def test_role_other_falls_back_to_default_microphone_when_loopback_missing(self):
        default_speaker = SimpleNamespace(id="alsa_output.missing")
        default_mic = SimpleNamespace(id="default-microphone", name="default microphone")
        sc = SimpleNamespace(
            default_speaker=mock.Mock(return_value=default_speaker),
            get_microphone=mock.Mock(side_effect=IndexError("no soundcard")),
            all_microphones=mock.Mock(return_value=[]),
            default_microphone=mock.Mock(return_value=default_mic),
        )

        with mock.patch.dict(sys.modules, {"soundcard": sc}):
            source = SoundcardSource(device=None, role="other", samplerate=16000)

        self.assertEqual(source.name, "default microphone")
        self.assertEqual(source.device_id, "default-microphone")
        sc.default_microphone.assert_called_once()  # pyright: ignore[reportAny]

    def test_explicit_device_uses_get_microphone_directly(self):
        selected = SimpleNamespace(id="my-device", name="explicit mic")
        sc = SimpleNamespace(
            get_microphone=mock.Mock(return_value=selected),
            default_speaker=mock.Mock(),
            all_microphones=mock.Mock(),
            default_microphone=mock.Mock(),
        )

        with mock.patch.dict(sys.modules, {"soundcard": sc}):
            source = SoundcardSource(device="my-device", role="self", samplerate=16000)

        self.assertEqual(source.name, "explicit mic")
        self.assertEqual(source.device_id, "my-device")
        sc.get_microphone.assert_called_once_with("my-device", include_loopback=True)  # pyright: ignore[reportAny]

    def test_role_self_uses_default_microphone(self):
        default_mic = SimpleNamespace(id="default-microphone", name="default microphone")
        sc = SimpleNamespace(
            default_microphone=mock.Mock(return_value=default_mic),
            default_speaker=mock.Mock(),
            get_microphone=mock.Mock(),
            all_microphones=mock.Mock(),
        )

        with mock.patch.dict(sys.modules, {"soundcard": sc}):
            source = SoundcardSource(device=None, role="self", samplerate=16000)

        self.assertEqual(source.name, "default microphone")
        self.assertEqual(source.device_id, "default-microphone")
        sc.default_microphone.assert_called_once_with()  # pyright: ignore[reportAny]
        sc.default_speaker.assert_not_called()  # pyright: ignore[reportAny]
        sc.get_microphone.assert_not_called()  # pyright: ignore[reportAny]

    def test_open_requests_30_ms_blocksize_derived_from_samplerate(self):
        microphone = _FakeMicrophone(_FakeRecorder())
        sc = _FakeSoundcard(microphone)

        with mock.patch.dict(sys.modules, {"soundcard": sc}):
            for samplerate, expected_blocksize in ((16_000, 480), (48_000, 1_440)):
                with self.subTest(samplerate=samplerate):
                    source = SoundcardSource(device="microphone", role="self", samplerate=samplerate)

                    with source.open():
                        pass

                    self.assertEqual(
                        microphone.recorder_calls[-1],
                        (samplerate, 1, expected_blocksize),
                    )

    def test_read_forwards_numframes_and_returns_mono_float32(self):
        data = np.array([[0.0, 1.0], [0.5, -0.5], [-1.0, 1.0]], dtype=np.float64)
        recorder = _FakeRecorder(data)
        microphone = _FakeMicrophone(recorder)
        sc = _FakeSoundcard(microphone)

        with mock.patch.dict(sys.modules, {"soundcard": sc}):
            source = SoundcardSource(device="microphone", role="self", samplerate=16000)

        with source.open() as reader:
            mono = reader.read(3)

        self.assertEqual(recorder.numframes, [3])
        self.assertEqual(mono.shape, (3,))
        self.assertEqual(mono.dtype, np.dtype(np.float32))
        np.testing.assert_array_equal(mono, np.array([0.5, 0.0, 0.0], dtype=np.float32))


if __name__ == "__main__":
    _ = unittest.main()
