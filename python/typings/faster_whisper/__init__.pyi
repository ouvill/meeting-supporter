from collections.abc import Iterable

import numpy as np

class Segment:
    text: str
    avg_logprob: float
    compression_ratio: float
    no_speech_prob: float

class TranscriptionInfo:
    pass

class WhisperModel:
    def __init__(
        self,
        model_size_or_path: str,
        device: str = "auto",
        compute_type: str = "float16",
        **kwargs: object,
    ) -> None: ...
    def transcribe(
        self,
        audio: str | object | np.ndarray,
        *,
        language: str | None = None,
        beam_size: int = 5,
        temperature: float = 0,
        condition_on_previous_text: bool = True,
        no_speech_threshold: float = 0.6,
        log_prob_threshold: float = -1.0,
        compression_ratio_threshold: float = 2.4,
        vad_filter: bool = False,
        **kwargs: object,
    ) -> tuple[Iterable[Segment], TranscriptionInfo]: ...
