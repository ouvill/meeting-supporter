# pyright: reportUninitializedInstanceVariable=false
import dataclasses
import queue
import re
import unittest
from typing import override

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.stt import SttPipeline, build_pipeline


async def _noop_broadcast(_msg: object) -> None:
    return None


async def _noop_handle_speech(_role: str, _text: str) -> None:
    return None


def _make_queue() -> "queue.Queue[AudioFrame | None]":
    return queue.Queue()


class FactoryRoutingTest(unittest.TestCase):
    base_cfg: SttConfig

    @override
    def setUp(self) -> None:
        self.base_cfg = SttConfig(
            backend="whisper",
            whisper_model="small",
            deepgram_model="nova-3",
            language="ja",
            vad_sensitivity=0.4,
            silence_duration=0.4,
            vad_aggressiveness=2,
            device="auto",
            remote_url="ws://localhost:8001/ws/stt",
            remote_token="",
            sample_rate=16000,
            chunk_size=1600,
        )

    def _cfg(self, backend: str) -> SttConfig:
        return dataclasses.replace(self.base_cfg, backend=backend)

    def test_supported_backends_return_pipeline_with_public_prewarm_capability(self) -> None:
        for backend in (
            "whisper",
            "reazonspeech",
            "dummy",
        ):
            with self.subTest(backend=backend):
                pipeline = build_pipeline(
                    _make_queue(),
                    "other",
                    self._cfg(backend),
                    _noop_broadcast,
                    _noop_handle_speech,
                )

                self.assertIsInstance(pipeline, SttPipeline)
                self.assertEqual(pipeline.supports_prewarm(), backend in {"whisper", "reazonspeech"})

    def test_retired_cloud_backends_cannot_create_a_pipeline(self) -> None:
        for backend in ("deepgram", "openai", "xai", "remote", "managed"):
            with self.subTest(backend=backend), self.assertRaisesRegex(ValueError, "端末内の方式"):
                _ = build_pipeline(_make_queue(), "other", self._cfg(backend), _noop_broadcast, _noop_handle_speech)
            with self.subTest(direct=backend), self.assertRaisesRegex(ValueError, "端末内の方式"):
                _ = SttPipeline(_make_queue(), self._cfg(backend), "other", _noop_broadcast, _noop_handle_speech)

    def test_invalid_backends_raise_exact_errors(self) -> None:
        supported = "whisper / reazonspeech / dummy"
        cases = (
            ("local", f"app.stt では local バックエンドは未対応です ({supported} を使用してください)"),
            ("mystery", "以前の音声認識設定は利用できません。端末内の方式を選び直してください。"),
        )
        for backend, message in cases:
            with self.subTest(backend=backend), self.assertRaisesRegex(ValueError, f"^{re.escape(message)}$"):
                _ = build_pipeline(
                    _make_queue(),
                    "other",
                    self._cfg(backend),
                    _noop_broadcast,
                    _noop_handle_speech,
                )


if __name__ == "__main__":
    _ = unittest.main()
