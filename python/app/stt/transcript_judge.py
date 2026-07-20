"""Pure Whisper transcript keep/drop judgment.

The Whisper stage supplies audio evidence and segment metrics; this module owns the
final auditable decision so phrase priors never become hidden hard rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.core.config import SttConfig

DEFAULT_SUSPICIOUS_PHRASES: tuple[str, ...] = (
    "ご視聴ありがとうございました",
    "ありがとうございました",
    "おやすみなさい",
)
_HALLUCINATION_STRIP_CHARS = "。、，,.．!！?？「」『』（）()[]【】"


class SegmentMetrics(Protocol):
    avg_logprob: float
    compression_ratio: float
    no_speech_prob: float


@dataclass(frozen=True)
class AudioEvidence:
    voiced_ms: int
    voiced_ratio: float
    rms_dbfs: float
    duration_ms: int


@dataclass(frozen=True)
class WhisperEvidence:
    segment_count: int
    avg_logprob: float
    max_no_speech_prob: float
    max_compression_ratio: float


@dataclass(frozen=True)
class TranscriptEvidence:
    text: str
    audio: AudioEvidence
    whisper: WhisperEvidence


@dataclass(frozen=True)
class TranscriptDecision:
    keep: bool
    score: float
    reasons: tuple[str, ...]


def normalize_transcript(text: str) -> str:
    return "".join(text.split()).strip(_HALLUCINATION_STRIP_CHARS)


def whisper_evidence_from_segments(segments: Sequence[SegmentMetrics]) -> WhisperEvidence:
    if not segments:
        return WhisperEvidence(
            segment_count=0,
            avg_logprob=0.0,
            max_no_speech_prob=0.0,
            max_compression_ratio=0.0,
        )
    return WhisperEvidence(
        segment_count=len(segments),
        avg_logprob=sum(segment.avg_logprob for segment in segments) / len(segments),
        max_no_speech_prob=max(segment.no_speech_prob for segment in segments),
        max_compression_ratio=max(segment.compression_ratio for segment in segments),
    )


def judge_transcript(cfg: SttConfig, evidence: TranscriptEvidence) -> TranscriptDecision:
    reasons: list[str] = []
    normalized = normalize_transcript(evidence.text)
    audio = evidence.audio
    whisper = evidence.whisper

    if not normalized:
        reasons.append("empty_text")
        return TranscriptDecision(keep=False, score=1.0, reasons=tuple(reasons))
    if whisper.segment_count == 0:
        reasons.append("no_segments")
        return TranscriptDecision(keep=False, score=1.0, reasons=tuple(reasons))
    if audio.voiced_ms < cfg.hard_min_voiced_ms:
        reasons.append("too_short_audio")
        return TranscriptDecision(keep=False, score=1.0, reasons=tuple(reasons))
    if whisper.max_no_speech_prob >= cfg.hard_no_speech_threshold:
        reasons.append("hard_no_speech")
        return TranscriptDecision(keep=False, score=1.0, reasons=tuple(reasons))
    if whisper.avg_logprob <= cfg.hard_logprob_threshold:
        reasons.append("hard_low_logprob")
        return TranscriptDecision(keep=False, score=1.0, reasons=tuple(reasons))
    if whisper.max_compression_ratio >= cfg.hard_compression_ratio_threshold:
        reasons.append("hard_high_compression")
        return TranscriptDecision(keep=False, score=1.0, reasons=tuple(reasons))

    score = 0.0
    categories: set[str] = set()

    if audio.voiced_ms < cfg.soft_min_voiced_ms:
        reasons.append("short_audio")
        score += 0.40
        categories.add("audio")
    if audio.voiced_ratio < cfg.soft_min_voiced_ratio:
        reasons.append("low_voiced_ratio")
        score += 0.35
        categories.add("audio")
    if audio.rms_dbfs < cfg.soft_min_rms_dbfs:
        reasons.append("quiet_audio")
        score += 0.25
        categories.add("audio")
    if whisper.max_no_speech_prob >= cfg.soft_no_speech_threshold:
        reasons.append("maybe_no_speech")
        score += 0.45
        categories.add("model")
    if whisper.avg_logprob <= cfg.soft_logprob_threshold:
        reasons.append("low_confidence")
        score += 0.45
        categories.add("model")
    if whisper.max_compression_ratio >= cfg.soft_compression_ratio_threshold:
        reasons.append("repetitive_text")
        score += 0.35
        categories.add("model")

    for phrase in cfg.suspicious_phrases:
        suspicious = normalize_transcript(str(phrase))
        if not suspicious:
            continue
        if normalized == suspicious:
            reasons.append("suspicious_phrase_exact")
            score += 0.35
            categories.add("text")
            break
        if suspicious in normalized:
            reasons.append("suspicious_phrase_substring")
            score += 0.20
            categories.add("text")
            break

    if score >= cfg.drop_score_threshold and len(categories) >= 2:
        reasons.append("multiple_weak_signals")
        return TranscriptDecision(keep=False, score=score, reasons=tuple(reasons))

    return TranscriptDecision(keep=True, score=score, reasons=tuple(reasons))
