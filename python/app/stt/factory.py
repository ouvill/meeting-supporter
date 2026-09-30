"""Factory for building SttPipeline instances."""

import os
import queue

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import OutgoingBroadcastFn
from app.core.types import HandleSpeechFn
from app.stt.native_pipeline import NativeSttPipeline
from app.stt.pipeline import SttPipeline

_SUPPORTED_BACKENDS = {"whisper", "reazonspeech", "dummy"}


def build_pipeline(
    stt_queue: "queue.Queue[AudioFrame | None]",
    role: str,
    cfg: SttConfig,
    broadcast_fn: OutgoingBroadcastFn,
    handle_speech_fn: HandleSpeechFn,
) -> SttPipeline | NativeSttPipeline:
    supported = "whisper / reazonspeech / dummy"
    if cfg.backend == "local":
        raise ValueError(f"app.stt では local バックエンドは未対応です ({supported} を使用してください)")
    if cfg.backend not in _SUPPORTED_BACKENDS:
        raise ValueError("以前の音声認識設定は利用できません。端末内の方式を選び直してください。")
    if cfg.backend == "reazonspeech":
        runtime = os.environ.get("MEETING_REAZON_RUNTIME", "python")
        if runtime not in {"python", "rust"}:
            raise ValueError("MEETING_REAZON_RUNTIME は python または rust を指定してください。")
        if runtime == "rust":
            return NativeSttPipeline(stt_queue, cfg, role, broadcast_fn, handle_speech_fn)
    return SttPipeline(
        stt_queue,
        cfg,
        role,
        broadcast_fn,
        handle_speech_fn,
    )


__all__ = ["build_pipeline"]
