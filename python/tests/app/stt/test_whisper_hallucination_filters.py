# pyright: reportPrivateUsage=false, reportUninitializedInstanceVariable=false, reportAny=false
import queue
import unittest
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast, override
from unittest.mock import Mock, patch

import numpy as np

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.publisher import OutgoingPublisher
from app.stt.stages.stt_whisper import WhisperEngine, WhisperStage
from app.stt.transcript_judge import (
    AudioEvidence,
    SegmentMetrics,
    TranscriptEvidence,
    judge_transcript,
    whisper_evidence_from_segments,
)

if TYPE_CHECKING:
    from faster_whisper import WhisperModel


def _make_config() -> SttConfig:
    return SttConfig(
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


def _seg(avg_logprob: float, no_speech_prob: float, compression_ratio: float) -> SegmentMetrics:
    return cast(
        SegmentMetrics,
        cast(
            object,
            SimpleNamespace(
                avg_logprob=avg_logprob,
                no_speech_prob=no_speech_prob,
                compression_ratio=compression_ratio,
            ),
        ),
    )


def _evidence(
    text: str,
    audio: AudioEvidence,
    *,
    avg_logprob: float = -0.1,
    no_speech_prob: float = 0.02,
    compression_ratio: float = 1.1,
) -> TranscriptEvidence:
    return TranscriptEvidence(
        text=text,
        audio=audio,
        whisper=whisper_evidence_from_segments([_seg(avg_logprob, no_speech_prob, compression_ratio)]),
    )


class WhisperTranscriptJudgeTest(unittest.TestCase):
    cfg: SttConfig
    healthy_audio: AudioEvidence

    @override
    def setUp(self) -> None:
        self.cfg = _make_config()
        self.healthy_audio = AudioEvidence(480, 0.8, -20.0, 600)

    def test_keeps_healthy_exact_suspicious_phrase(self) -> None:
        decision = judge_transcript(
            self.cfg,
            _evidence("ご視聴ありがとうございました", self.healthy_audio),
        )

        self.assertTrue(decision.keep)
        self.assertIn("suspicious_phrase_exact", decision.reasons)

    def test_keeps_healthy_substring_suspicious_phrase(self) -> None:
        decision = judge_transcript(
            self.cfg,
            _evidence("動画の最後にご視聴ありがとうございましたと表示されます", self.healthy_audio),
        )

        self.assertTrue(decision.keep)
        self.assertIn("suspicious_phrase_substring", decision.reasons)

    def test_drops_exact_phrase_with_weak_model_evidence(self) -> None:
        decision = judge_transcript(
            self.cfg,
            _evidence("ご視聴ありがとうございました", self.healthy_audio, no_speech_prob=0.65),
        )

        self.assertFalse(decision.keep)
        self.assertIn("suspicious_phrase_exact", decision.reasons)
        self.assertIn("maybe_no_speech", decision.reasons)
        self.assertIn("multiple_weak_signals", decision.reasons)

    def test_drops_substring_phrase_with_weak_model_evidence(self) -> None:
        decision = judge_transcript(
            self.cfg,
            _evidence(
                "動画の最後にご視聴ありがとうございましたと表示されます", self.healthy_audio, no_speech_prob=0.65
            ),
        )

        self.assertFalse(decision.keep)
        self.assertIn("suspicious_phrase_substring", decision.reasons)
        self.assertIn("maybe_no_speech", decision.reasons)
        self.assertIn("multiple_weak_signals", decision.reasons)

    def test_drops_non_phrase_hard_low_confidence(self) -> None:
        decision = judge_transcript(
            self.cfg,
            _evidence("今日の議題を始めます", self.healthy_audio, avg_logprob=-2.2),
        )

        self.assertFalse(decision.keep)
        self.assertEqual(decision.reasons, ("hard_low_logprob",))

    def test_drops_non_phrase_hard_high_no_speech(self) -> None:
        decision = judge_transcript(
            self.cfg,
            _evidence("今日の議題を始めます", self.healthy_audio, no_speech_prob=0.9),
        )

        self.assertFalse(decision.keep)
        self.assertEqual(decision.reasons, ("hard_no_speech",))

    def test_drops_non_phrase_hard_high_compression(self) -> None:
        decision = judge_transcript(
            self.cfg,
            _evidence("今日の議題を始めます", self.healthy_audio, compression_ratio=3.8),
        )

        self.assertFalse(decision.keep)
        self.assertEqual(decision.reasons, ("hard_high_compression",))

    def test_keeps_non_phrase_single_soft_signal(self) -> None:
        decision = judge_transcript(
            self.cfg,
            _evidence("今日の議題を始めます", self.healthy_audio, no_speech_prob=0.62),
        )

        self.assertTrue(decision.keep)
        self.assertIn("maybe_no_speech", decision.reasons)

    def test_drops_non_phrase_two_category_weak_signals(self) -> None:
        weak_audio = AudioEvidence(480, 0.2, -20.0, 600)
        decision = judge_transcript(
            self.cfg,
            _evidence("今日の議題を始めます", weak_audio, no_speech_prob=0.62),
        )

        self.assertFalse(decision.keep)
        self.assertIn("maybe_no_speech", decision.reasons)
        self.assertIn("low_voiced_ratio", decision.reasons)
        self.assertIn("multiple_weak_signals", decision.reasons)

    def test_empty_text_and_empty_segments_are_hard_failures(self) -> None:
        empty_text = judge_transcript(
            self.cfg,
            _evidence("   。", self.healthy_audio),
        )
        no_segments = judge_transcript(
            self.cfg,
            TranscriptEvidence(
                text="今日の議題を始めます",
                audio=self.healthy_audio,
                whisper=whisper_evidence_from_segments([]),
            ),
        )

        self.assertFalse(empty_text.keep)
        self.assertEqual(empty_text.reasons, ("empty_text",))
        self.assertFalse(no_segments.keep)
        self.assertEqual(no_segments.reasons, ("no_segments",))


class WhisperStageGateTest(unittest.TestCase):
    def test_should_enqueue_segment_rejects_too_short_audio(self) -> None:
        should_enqueue = WhisperStage._should_enqueue(
            _make_config(),
            AudioEvidence(voiced_ms=60, voiced_ratio=0.8, rms_dbfs=-20.0, duration_ms=300),
        )

        self.assertFalse(should_enqueue)

    def test_should_enqueue_segment_accepts_quiet_audio_at_hard_minimum(self) -> None:
        should_enqueue = WhisperStage._should_enqueue(
            _make_config(),
            AudioEvidence(voiced_ms=120, voiced_ratio=0.8, rms_dbfs=-60.0, duration_ms=300),
        )

        self.assertTrue(should_enqueue)


class WhisperStagePreRollTest(unittest.TestCase):
    def test_prepends_last_150ms_without_changing_voiced_gate(self) -> None:
        cfg = _make_config()
        cfg.silence_duration = 0.06
        in_q: queue.Queue[AudioFrame | None] = queue.Queue()
        pre_roll = [
            AudioFrame(
                pcm=value.to_bytes(2, "little", signed=True) * 480,
                is_speech=False,
                timestamp_ms=index * 30.0,
            )
            for index, value in enumerate(range(1, 7))
        ]
        speech = [
            AudioFrame(
                pcm=(12000).to_bytes(2, "little", signed=True) * 480,
                is_speech=True,
                timestamp_ms=(index + 6) * 30.0,
            )
            for index in range(4)
        ]
        trailing = [
            AudioFrame(
                pcm=b"\x00\x00" * 480,
                is_speech=False,
                timestamp_ms=(index + 10) * 30.0,
            )
            for index in range(2)
        ]
        for frame in [*pre_roll, *speech, *trailing]:
            in_q.put(frame)
        in_q.put(None)

        async def handle_speech(_role: str, _text: str) -> None:
            pass

        publisher = cast(OutgoingPublisher, Mock())
        stage = WhisperStage(in_q, cfg, "other", publisher, handle_speech)
        with patch.object(stage, "_enqueue_audio") as enqueue:
            stage.start()
            stage.join(timeout=1)

        self.assertFalse(stage.running)
        audio_np = cast(np.ndarray[tuple[int], np.dtype[np.float32]], enqueue.call_args.args[0])
        evidence = cast(AudioEvidence, enqueue.call_args.args[1])
        self.assertEqual(audio_np.size, 11 * 480)
        self.assertAlmostEqual(float(audio_np[0]), 2 / 32767.0)
        self.assertAlmostEqual(float(audio_np[5 * 480]), 12000 / 32767.0)
        self.assertEqual(evidence.voiced_ms, 120)
        self.assertAlmostEqual(evidence.voiced_ratio, 4 / 6)
        self.assertEqual(evidence.duration_ms, 330)

    def test_each_close_utterance_gets_the_immediately_preceding_five_frames(self) -> None:
        cfg = _make_config()
        cfg.silence_duration = 0.06

        def make_frame(value: int, is_speech: bool, index: int) -> AudioFrame:
            return AudioFrame(
                pcm=value.to_bytes(2, "little", signed=True) * 480,
                is_speech=is_speech,
                timestamp_ms=index * 30.0,
            )

        history = [make_frame(value, False, index) for index, value in enumerate(range(1, 6))]
        first_speech = [make_frame(value, True, index + 5) for index, value in enumerate(range(101, 105))]
        first_trailing = [make_frame(value, False, index + 9) for index, value in enumerate(range(201, 203))]
        second_speech = [make_frame(value, True, index + 11) for index, value in enumerate(range(301, 305))]
        second_trailing = [make_frame(value, False, index + 15) for index, value in enumerate(range(401, 403))]
        frames = [*history, *first_speech, *first_trailing, *second_speech, *second_trailing]
        in_q: queue.Queue[AudioFrame | None] = queue.Queue()
        for frame in frames:
            in_q.put(frame)
        in_q.put(None)

        async def handle_speech(_role: str, _text: str) -> None:
            pass

        stage = WhisperStage(in_q, cfg, "other", cast(OutgoingPublisher, Mock()), handle_speech)
        with patch.object(stage, "_enqueue_audio") as enqueue:
            stage.start()
            stage.join(timeout=1)

        self.assertFalse(stage.running)
        self.assertEqual(enqueue.call_count, 2)
        expected_segments = [
            [*history, *first_speech, *first_trailing],
            [*first_speech[-3:], *first_trailing, *second_speech, *second_trailing],
        ]
        for call, expected_frames in zip(enqueue.call_args_list, expected_segments, strict=True):
            actual = cast(np.ndarray[tuple[int], np.dtype[np.float32]], call.args[0])
            expected = (
                np.frombuffer(b"".join(frame.pcm for frame in expected_frames), dtype=np.int16).astype(np.float32)
                / 32767.0
            )
            np.testing.assert_array_equal(actual, expected)

    def test_preroll_volume_does_not_change_segment_rms_evidence(self) -> None:
        cfg = _make_config()
        cfg.silence_duration = 0.06
        speech_pcm = (1200).to_bytes(2, "little", signed=True) * 480
        silence_pcm = b"\x00\x00" * 480

        def run_with_preroll(preroll_pcm: bytes) -> AudioEvidence:
            frames = [
                *[AudioFrame(pcm=preroll_pcm, is_speech=False, timestamp_ms=index * 30.0) for index in range(5)],
                *[AudioFrame(pcm=speech_pcm, is_speech=True, timestamp_ms=(index + 5) * 30.0) for index in range(4)],
                *[AudioFrame(pcm=silence_pcm, is_speech=False, timestamp_ms=(index + 9) * 30.0) for index in range(2)],
            ]
            in_q: queue.Queue[AudioFrame | None] = queue.Queue()
            for frame in frames:
                in_q.put(frame)
            in_q.put(None)

            async def handle_speech(_role: str, _text: str) -> None:
                pass

            stage = WhisperStage(in_q, cfg, "other", cast(OutgoingPublisher, Mock()), handle_speech)
            with patch.object(stage, "_enqueue_audio") as enqueue:
                stage.start()
                stage.join(timeout=1)
            self.assertFalse(stage.running)
            return cast(AudioEvidence, enqueue.call_args.args[1])

        quiet_preroll_evidence = run_with_preroll(silence_pcm)
        loud_preroll_evidence = run_with_preroll((30000).to_bytes(2, "little", signed=True) * 480)
        owned_audio = (
            np.frombuffer(b"".join([speech_pcm] * 4 + [silence_pcm] * 2), dtype=np.int16).astype(np.float32) / 32767.0
        )
        expected_rms = WhisperStage._rms_dbfs(owned_audio)

        self.assertAlmostEqual(quiet_preroll_evidence.rms_dbfs, expected_rms)
        self.assertAlmostEqual(loud_preroll_evidence.rms_dbfs, expected_rms)


class WhisperTranscribeThresholdTest(unittest.TestCase):
    def test_transcribe_uses_decode_thresholds_and_disables_vad_filter(self) -> None:
        cfg = _make_config()
        cfg.decode_no_speech_threshold = 0.99
        cfg.decode_log_prob_threshold = -9.5
        cfg.decode_compression_ratio_threshold = 9.0
        transcribe = Mock(return_value=([], object()))
        fake_model = SimpleNamespace(transcribe=transcribe)
        stage = cast(WhisperStage, cast(object, SimpleNamespace(cfg=cfg)))

        try:
            WhisperEngine._model = cast("WhisperModel", cast(object, fake_model))
            _ = WhisperEngine._transcribe_segments(stage, np.zeros(1600, dtype=np.float32))
        finally:
            WhisperEngine._model = None

        transcribe.assert_called_once()
        kwargs = transcribe.call_args.kwargs
        self.assertEqual(kwargs["no_speech_threshold"], cfg.decode_no_speech_threshold)
        self.assertEqual(kwargs["log_prob_threshold"], cfg.decode_log_prob_threshold)
        self.assertEqual(kwargs["compression_ratio_threshold"], cfg.decode_compression_ratio_threshold)
        self.assertFalse(kwargs["vad_filter"])


if __name__ == "__main__":
    _ = unittest.main()
