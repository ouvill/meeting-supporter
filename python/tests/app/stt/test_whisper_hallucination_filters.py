# pyright: reportPrivateUsage=false, reportUninitializedInstanceVariable=false, reportAny=false
import unittest
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast, override
from unittest.mock import Mock

import numpy as np

from app.core.config import SttConfig
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
