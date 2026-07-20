"""OpenAI transcription stage consuming VAD-annotated PCM16 frames."""

from __future__ import annotations

import io
import json
import os
import queue
import secrets
import urllib.error
import urllib.request
import wave
from collections import deque
from typing import Protocol, Self, cast, override

from app.audio.base import AudioFrame, PipelineStage
from app.core.config import SttConfig
from app.core.messages import ErrorMsg
from app.core.publisher import OutgoingPublisher
from app.core.types import HandleSpeechFn

_API_URL = "https://api.openai.com/v1/audio/transcriptions"
_FRAME_MS = 30
_PREROLL_MS = 300
_REQUEST_TIMEOUT_SECONDS = 30.0


class _ReadableResponse(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> bool | None: ...

    def read(self) -> bytes: ...


class OpenAIStage(PipelineStage):
    """Upload each VAD-delimited PCM16 utterance to OpenAI as a mono WAV file."""

    def __init__(
        self,
        in_q: queue.Queue[AudioFrame | None],
        cfg: SttConfig,
        role: str,
        publisher: OutgoingPublisher,
        handle_speech_fn: HandleSpeechFn,
    ) -> None:
        super().__init__()
        self._in_q: queue.Queue[AudioFrame | None] = in_q
        self._cfg: SttConfig = cfg
        self._role: str = role
        self._publisher: OutgoingPublisher = publisher
        self._handle_speech: HandleSpeechFn = handle_speech_fn

    @override
    def _run(self) -> None:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            self._publisher.publish(ErrorMsg(text="OPENAI_API_KEY が未設定です"))
            return

        silence_threshold = max(1, int(float(self._cfg.silence_duration) * 1000 / _FRAME_MS))
        preroll: deque[bytes] = deque(maxlen=max(1, _PREROLL_MS // _FRAME_MS))
        utterance: list[bytes] = []
        in_speech = False
        silence_frames = 0

        def submit_pending() -> None:
            nonlocal in_speech, silence_frames
            if utterance:
                self._transcribe(b"".join(utterance), api_key)
            utterance.clear()
            in_speech = False
            silence_frames = 0

        while True:
            try:
                frame = self._in_q.get(timeout=0.1)
            except queue.Empty:
                if self._stop_event.is_set():
                    submit_pending()
                    return
                continue

            if frame is None:
                submit_pending()
                return

            if frame.is_speech:
                if not in_speech:
                    utterance.extend(preroll)
                    preroll.clear()
                    in_speech = True
                utterance.append(frame.pcm)
                silence_frames = 0
            elif in_speech:
                utterance.append(frame.pcm)
                silence_frames += 1
                if silence_frames >= silence_threshold:
                    submit_pending()
            else:
                preroll.append(frame.pcm)

            if self._stop_event.is_set():
                submit_pending()
                return

    def _transcribe(self, pcm: bytes, api_key: str) -> None:
        try:
            wav_data = self._to_wav(pcm)
            request = self._build_request(wav_data, api_key)
            response = cast(_ReadableResponse, urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS))
            with response:
                raw_response: bytes = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                self._publisher.publish(ErrorMsg(text=f"OpenAI STT認証エラー({self._role})"))
            else:
                self._publisher.publish(ErrorMsg(text=f"OpenAI STT HTTPエラー({self._role}): {exc.code}"))
            return
        except (urllib.error.URLError, OSError):
            self._publisher.publish(ErrorMsg(text=f"OpenAI STT通信エラー({self._role})"))
            return

        try:
            decoded = cast(object, json.loads(raw_response.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._publisher.publish(ErrorMsg(text=f"OpenAI STT応答エラー({self._role})"))
            return

        if not isinstance(decoded, dict):
            self._publisher.publish(ErrorMsg(text=f"OpenAI STT応答エラー({self._role})"))
            return
        payload = cast(dict[str, object], decoded)
        text = payload.get("text")
        if not isinstance(text, str):
            self._publisher.publish(ErrorMsg(text=f"OpenAI STT応答エラー({self._role})"))
            return
        text = text.strip()
        if text:
            self._publisher.schedule(self._handle_speech(self._role, text))

    def _to_wav(self, pcm: bytes) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self._cfg.sample_rate)
            wav.writeframes(pcm)
        return output.getvalue()

    def _build_request(self, wav_data: bytes, api_key: str) -> urllib.request.Request:
        boundary = f"----meeting-supporter-{secrets.token_hex(16)}"
        body = self._multipart_body(boundary, wav_data)
        return urllib.request.Request(
            _API_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )

    def _multipart_body(self, boundary: str, wav_data: bytes) -> bytes:
        fields: tuple[tuple[str, str], ...] = (("model", self._cfg.openai_model), ("language", self._cfg.language))
        parts: list[bytes] = []
        for name, value in fields:
            parts.extend(
                (
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    value.encode("utf-8"),
                    b"\r\n",
                )
            )
        parts.extend(
            (
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="file"; filename="utterance.wav"\r\n',
                b"Content-Type: audio/wav\r\n\r\n",
                wav_data,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            )
        )
        return b"".join(parts)


__all__ = ["OpenAIStage"]
