#!/usr/bin/env python3
"""
会議支援AI — Google Cloud STT リモートサーバー

WebSocket で音声 PCM を受信し、Google Cloud Speech-to-Text v2 で認識して結果を返す。
セッション上限（~55秒）はサーバー内部で自動再起動するため、クライアントは接続を維持したまま
連続的に音声を送信し続けるだけでよい。

認証（将来対応）:
  STT_API_TOKEN 環境変数を設定すると、WebSocket 接続時に
    - Authorization: Bearer <token>  ヘッダー
    - ?token=<token>                 クエリパラメータ
  のいずれかで認証を要求する。未設定時は認証なし。

環境変数:
  GOOGLE_CLOUD_PROJECT           プロジェクト ID (必須)
  GOOGLE_APPLICATION_CREDENTIALS サービスアカウント JSON パス
  STT_HOST                       バインドホスト (default: 0.0.0.0)
  STT_PORT                       バインドポート (default: 8001)
  STT_API_TOKEN                  Bearer トークン (未設定時は認証なし)
  MAX_SESSION_SECONDS            Google STT セッション最大秒数 (default: 55)
"""

import asyncio
import json
import os
import queue
import threading
import time
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

load_dotenv()

# ── 設定 ──────────────────────────────────────────────────────────────────────

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.default.toml"
_APP_DATA_DIR        = os.environ.get("APP_DATA_DIR")
_USER_CONFIG_PATH    = (Path(_APP_DATA_DIR) / "config.toml") if _APP_DATA_DIR else None


def _load_config() -> dict:
    if _USER_CONFIG_PATH and _USER_CONFIG_PATH.exists():
        with open(_USER_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    if _DEFAULT_CONFIG_PATH.exists():
        with open(_DEFAULT_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    return {}


_cfg = _load_config()

GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
STT_HOST             = os.environ.get("STT_HOST", "0.0.0.0")
STT_PORT             = int(os.environ.get("STT_PORT", str(_cfg.get("server", {}).get("port", 8001))))
STT_API_TOKEN        = os.environ.get("STT_API_TOKEN", "")
MAX_SESSION_SECONDS  = int(
    os.environ.get(
        "MAX_SESSION_SECONDS",
        str(_cfg.get("audio", {}).get("max_session_seconds", 55)),
    )
)

# ── FastAPI ───────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not GOOGLE_CLOUD_PROJECT:
        print("[WARN] GOOGLE_CLOUD_PROJECT が未設定です")
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        print("[INFO] GOOGLE_APPLICATION_CREDENTIALS 未設定 — ADC (Application Default Credentials) を使用します")
    auth_status = f"有効 (token={STT_API_TOKEN[:4]}...)" if STT_API_TOKEN else "無効（STT_API_TOKEN 未設定）"
    print(f"[INFO] 認証: {auth_status}")
    print(f"[INFO] Google STT セッション上限: {MAX_SESSION_SECONDS}秒")
    print(f"会議支援AI STTサーバー 起動完了  http://{STT_HOST}:{STT_PORT}")
    yield


app = FastAPI(title="Meeting Supporter STT Server", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ── 認証ヘルパー ───────────────────────────────────────────────────────────────


def _extract_token(authorization: str | None, token_query: str | None) -> str | None:
    """Authorization ヘッダーまたは ?token= クエリからトークンを取り出す。"""
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return token_query


def _check_auth(token: str | None) -> bool:
    """STT_API_TOKEN 未設定時は常に許可。設定時はトークンを検証する。"""
    if not STT_API_TOKEN:
        return True
    return token == STT_API_TOKEN


# ── WebSocket STT エンドポイント ───────────────────────────────────────────────


@app.websocket("/ws/stt")
async def stt_endpoint(
    ws: WebSocket,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    if not _check_auth(_extract_token(authorization, token)):
        await ws.close(code=4001, reason="Unauthorized")
        return

    await ws.accept()

    try:
        config_raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        config = json.loads(config_raw)
    except (asyncio.TimeoutError, Exception):
        await ws.close(code=4000, reason="Invalid config")
        return

    sample_rate: int = int(config.get("sample_rate", 16000))
    language: str    = config.get("language", "ja-JP")

    if not GOOGLE_CLOUD_PROJECT:
        await ws.send_text(json.dumps({
            "type": "error",
            "text": "GOOGLE_CLOUD_PROJECT が未設定です",
        }))
        await ws.close()
        return

    await ws.send_text(json.dumps({"type": "ready"}))
    print(f"[INFO] STTセッション開始 rate={sample_rate} lang={language}")

    # Google STT セッションを内部で繰り返し再起動しながら接続を維持する
    while True:
        continued = await _run_stt_session(ws, sample_rate, language)
        if not continued:
            break
        # セッション上限に達したので直ちに次セッションを開始
        print("[INFO] Google STTセッション再起動")

    print("[INFO] STT接続終了")


async def _run_stt_session(
    ws: WebSocket,
    sample_rate: int,
    language: str,
) -> bool:
    """
    1 Google STT セッションを処理する。

    Returns:
        True  — セッション上限に達した。呼び出し元は次セッションを開始すること。
        False — クライアントが切断した。呼び出し元は接続を終了すること。
    """
    from google.cloud.speech_v2 import SpeechClient
    from google.cloud.speech_v2.types import cloud_speech

    audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=300)
    done_event   = threading.Event()
    session_start = time.perf_counter()
    loop = asyncio.get_event_loop()

    recognizer = f"projects/{GOOGLE_CLOUD_PROJECT}/locations/global/recognizers/_"
    streaming_config = cloud_speech.StreamingRecognitionConfig(
        config=cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=sample_rate,
                audio_channel_count=1,
            ),
            language_codes=[language],
            model="long",
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=True,
            ),
        ),
        streaming_features=cloud_speech.StreamingRecognitionFeatures(
            interim_results=True,
        ),
    )

    def _audio_gen():
        yield cloud_speech.StreamingRecognizeRequest(
            recognizer=recognizer,
            streaming_config=streaming_config,
        )
        while not done_event.is_set():
            try:
                chunk = audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if chunk is None:
                break
            yield cloud_speech.StreamingRecognizeRequest(audio=chunk)

    def _stt_worker() -> None:
        try:
            client = SpeechClient()
            for response in client.streaming_recognize(requests=_audio_gen()):
                for result in response.results:
                    text = result.alternatives[0].transcript
                    if not result.is_final:
                        asyncio.run_coroutine_threadsafe(
                            ws.send_text(json.dumps({"type": "interim", "text": text})),
                            loop,
                        )
                    else:
                        asyncio.run_coroutine_threadsafe(
                            ws.send_text(json.dumps({"type": "final", "text": text})),
                            loop,
                        )
        except Exception as e:
            asyncio.run_coroutine_threadsafe(
                ws.send_text(json.dumps({"type": "error", "text": str(e)})),
                loop,
            )
        finally:
            done_event.set()

    stt_thread = threading.Thread(target=_stt_worker, daemon=True)
    stt_thread.start()

    session_limit_hit = False
    try:
        while not done_event.is_set():
            elapsed = time.perf_counter() - session_start
            if elapsed >= MAX_SESSION_SECONDS:
                session_limit_hit = True
                break

            try:
                data = await asyncio.wait_for(ws.receive_bytes(), timeout=0.2)
                try:
                    audio_queue.put_nowait(data)
                except queue.Full:
                    pass
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                return False
            except Exception:
                return False
    finally:
        done_event.set()
        audio_queue.put(None)
        stt_thread.join(timeout=5)

    return session_limit_hit


# ── エントリポイント ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=STT_HOST, port=STT_PORT)