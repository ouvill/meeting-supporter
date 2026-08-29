"""Factory for building SttPipeline instances."""

import queue
from collections.abc import Callable

from app.audio.base import AudioFrame
from app.core.config import SttConfig
from app.core.messages import OutgoingBroadcastFn
from app.core.types import HandleSpeechFn
from app.services.managed_session import ManagedSessionStore
from app.stt.pipeline import SttPipeline

_SUPPORTED_BACKENDS = {
    "whisper",
    "reazonspeech",
    "remote",
    "deepgram",
    "managed",
    "openai",
    "xai",
    "dummy",
    "vosk",
}


def build_pipeline(
    stt_queue: "queue.Queue[AudioFrame | None]",
    role: str,
    cfg: SttConfig,
    broadcast_fn: OutgoingBroadcastFn,
    handle_speech_fn: HandleSpeechFn,
    managed_session_store: ManagedSessionStore | None = None,
    get_managed_session_id: Callable[[], str | None] | None = None,
) -> SttPipeline:
    supported = "whisper / reazonspeech / vosk / remote / deepgram / managed / openai / xai / dummy"
    if cfg.backend == "local":
        raise ValueError(f"app.stt では local バックエンドは未対応です ({supported} を使用してください)")
    if cfg.backend not in _SUPPORTED_BACKENDS:
        raise ValueError(f"未知のSTTバックエンド: {cfg.backend!r}  ({supported})")
    return SttPipeline(
        stt_queue,
        cfg,
        role,
        broadcast_fn,
        handle_speech_fn,
        managed_session_store,
        get_managed_session_id,
    )


__all__ = ["build_pipeline"]
