#!/usr/bin/env python3
"""Deepgram STT 動作確認スクリプト。

使い方:
  uv run python deepgram_check.py          # VADあり（デフォルト）
  uv run python deepgram_check.py --no-vad # VADなし（常時ストリーミング）
"""

import argparse
import asyncio
import collections
import json
import os
import queue
import threading
import time
from typing import TypedDict, cast

import numpy as np
import soundcard as sc
import websockets
from dotenv import load_dotenv

_ = load_dotenv()

# ── Deepgram JSON response types ────────────────────────────────────────────────


class _DGAlternative(TypedDict, total=False):
    transcript: str
    confidence: float


class _DGChannel(TypedDict, total=False):
    alternatives: list[_DGAlternative]


class _DGResult(TypedDict, total=False):
    type: str
    channel: _DGChannel
    is_final: bool
    speech_final: bool
    request_id: str


_FRAME_MS = 30
_SAMPLE_RATE = 16000
_VAD_AGGRESSIVENESS = 2
_SILENCE_DURATION = 0.8
_PREROLL_MS = 300
_KEEPALIVE_INTERVAL = 3.0
_MODEL = "nova-3"
_LANGUAGE = "ja"


async def main(use_vad: bool) -> None:
    api_key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not api_key:
        print("[ERROR] DEEPGRAM_API_KEY が未設定です")
        return

    mic = sc.default_microphone()
    mode_label = "VADあり（発話区間のみ送信 + Finalize）" if use_vad else "VADなし（常時ストリーミング）"
    print(f"[INFO] マイク  : {mic.name}")
    print(f"[INFO] モデル  : {_MODEL}  言語: {_LANGUAGE}")
    print(f"[INFO] モード  : {mode_label}")
    print("[INFO] 録音開始 — Ctrl+C で終了\n")

    frame_samples = int(_SAMPLE_RATE * _FRAME_MS / 1000)
    silence_threshold = max(1, int(_SILENCE_DURATION * 1000 / _FRAME_MS))
    preroll_size = max(1, _PREROLL_MS // _FRAME_MS)

    # VADあり: (pcm_bytes, is_speech) / VADなし: (pcm_bytes, True) 固定
    audio_queue: queue.Queue[tuple[bytes, bool]] = queue.Queue(maxsize=300)
    stop_event = threading.Event()

    def _record() -> None:
        vad = None
        if use_vad:
            import webrtcvad

            vad = webrtcvad.Vad(_VAD_AGGRESSIVENESS)

        with mic.recorder(samplerate=_SAMPLE_RATE, channels=1) as rec:
            while not stop_event.is_set():
                data = rec.record(numframes=frame_samples)
                mono = data.mean(axis=1) if data.ndim > 1 else data.reshape(-1)
                pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16)
                pcm_bytes = pcm.tobytes()

                if vad is not None:
                    try:
                        is_speech = vad.is_speech(pcm_bytes, _SAMPLE_RATE)
                    except Exception:
                        is_speech = False
                else:
                    is_speech = True  # VADなし: 常に送信

                try:
                    audio_queue.put_nowait((pcm_bytes, is_speech))
                except queue.Full:
                    pass

    record_thread = threading.Thread(target=_record, daemon=True)
    record_thread.start()

    params = "&".join(
        [
            f"model={_MODEL}",
            f"language={_LANGUAGE}",
            "encoding=linear16",
            f"sample_rate={_SAMPLE_RATE}",
            "channels=1",
            "interim_results=true",
            "endpointing=300",
        ]
    )
    url = f"wss://api.deepgram.com/v1/listen?{params}"
    headers = {"Authorization": f"Token {api_key}"}

    async with websockets.connect(url, additional_headers=headers) as ws:
        finalize_queue: asyncio.Queue[None] = asyncio.Queue()

        async def _send() -> None:
            last_keepalive = time.monotonic()
            in_speech = False
            silence_frames = 0
            preroll: collections.deque[bytes] = collections.deque(maxlen=preroll_size)

            while True:
                now = time.monotonic()
                try:
                    pcm_bytes, is_speech = audio_queue.get_nowait()
                    if is_speech:
                        if use_vad and not in_speech:
                            for frame in preroll:
                                await ws.send(frame)
                            preroll.clear()
                            print("[発話開始]", flush=True)
                        in_speech = True
                        silence_frames = 0
                        await ws.send(pcm_bytes)
                        last_keepalive = now
                    else:
                        if in_speech:
                            silence_frames += 1
                            await ws.send(pcm_bytes)
                            last_keepalive = now
                            if silence_frames >= silence_threshold:
                                await ws.send(json.dumps({"type": "Finalize"}))
                                finalize_queue.put_nowait(None)
                                in_speech = False
                                silence_frames = 0
                        else:
                            preroll.append(pcm_bytes)
                            if now - last_keepalive >= _KEEPALIVE_INTERVAL:
                                await ws.send(json.dumps({"type": "KeepAlive"}))
                                last_keepalive = now
                except queue.Empty:
                    if now - last_keepalive >= _KEEPALIVE_INTERVAL:
                        await ws.send(json.dumps({"type": "KeepAlive"}))
                        last_keepalive = now
                    await asyncio.sleep(0.005)

        async def _recv() -> None:
            chunk_buf: list[str] = []
            pending_finalize = 0
            last_confidence = 0.0

            async for message in ws:
                while not finalize_queue.empty():
                    finalize_queue.get_nowait()
                    pending_finalize += 1

                if not isinstance(message, str):
                    continue
                data = cast(_DGResult, json.loads(message))
                msg_type = data.get("type")

                if msg_type == "Results":
                    channel = data.get("channel")
                    alts = channel.get("alternatives", []) if channel is not None else []
                    if not alts:
                        continue
                    text = alts[0].get("transcript", "").strip()
                    is_final = data.get("is_final", False)
                    speech_final = data.get("speech_final", False)
                    confidence = alts[0].get("confidence", 0.0)
                    if is_final:
                        if text:
                            chunk_buf.append(text)
                            last_confidence = confidence
                        should_flush = speech_final or pending_finalize > 0
                        if should_flush:
                            pending_finalize = max(0, pending_finalize - 1)
                            full_text = "".join(chunk_buf)
                            chunk_buf.clear()
                            if full_text:
                                print(
                                    f"\n[確定] {full_text}  (confidence={last_confidence:.2f})",
                                    flush=True,
                                )
                    else:
                        print(
                            f"[interim] {text}                    ",
                            end="\r",
                            flush=True,
                        )
                elif msg_type == "Metadata":
                    request_id = data.get("request_id", "")
                    print(
                        f"[Metadata] request_id={request_id}",
                        flush=True,
                    )
                elif msg_type == "Error":
                    print(f"[ERROR] {data}", flush=True)

        send_task = asyncio.create_task(_send())
        recv_task = asyncio.create_task(_recv())
        try:
            done, pending = await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
            _ = done, pending
        finally:
            _ = send_task.cancel()
            _ = recv_task.cancel()
            stop_event.set()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deepgram STT テスト")
    _ = parser.add_argument("--no-vad", action="store_true", help="VADを無効にして常時ストリーミング")
    ns = parser.parse_args()
    no_vad = cast(bool, ns.no_vad)
    try:
        asyncio.run(main(use_vad=not no_vad))
    except KeyboardInterrupt:
        print("\n終了")
