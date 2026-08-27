# ADR-002: STT パイプラインアーキテクチャ

- **ステータス**: Accepted
- **日付**: 2026-04-25

## 背景

現在の STT 実装（`stt_v2/`）は以下の問題を抱えている。

**1. 責務の混在**
各バックエンドクラス（`WhisperSttStream`, `DeepgramSttStream`）が音声取得・VAD・音声認識・ブロードキャストをすべて一クラスに持っている。VAD エンジンを変更するには各バックエンドクラスを修正する必要がある。

**2. soundcard の二重オープン**
`AudioLevelMonitor` が STT ストリームとは別に soundcard デバイスをオープンしている。同一デバイスを2つの接続で読み取っており、OS によっては競合・音量の不整合が起きうる。また `AudioLevelMonitor` は STT が停止していても動作させる必要があるため、現状ではライフサイクルが複雑になっている。

**3. エンジン切り替えコスト**
デバイス変更・VAD 変更・STT バックエンド変更のいずれも、ストリーム全体の停止と再起動が必要。VAD だけ変えたいのに Whisper モデルを再ロードする無駄がある。

## 決定

STT 処理を **キューで接続された独立したステージ（スレッド）のパイプライン** として設計する。

### データフロー

```mermaid
flowchart LR
    CAP[CaptureStage\n音声取得スレッド]
    CAP --> Q1[Q1\nAudioFrame]
    Q1 --> MUX[Multiplexer\nファンアウト]
    MUX --> Qa[Qa]
    MUX --> Qb[Qb]
    Qa --> VOL[VolumeStage\n音量計算]
    VOL --> WS1[WebSocket\n音量レベル]
    Qb --> VAD[VadStage\nis_speech 付与]
    VAD --> Q2[Q2\nAudioFrame]
    Q2 --> STT[SttStage\nWhisper / Vosk / Deepgram / Remote]
    STT --> Q3[Q3]
    Q3 --> DIAR[DiarizationStage\n話者分離 Optional]
    DIAR --> Q4[Q4]
    Q4 --> WS2[WebSocket\n認識結果]
```

各ステージは独立したスレッドで動作し、前後のステージとはキューのみで通信する。

### Multiplexer（Q1 ファンアウト）

Python の `queue.Queue` はシングルコンシューマー前提。`VolumeStage` と `VadStage` が同一の Q1 を読むと片方しかフレームを受け取れない。そのため Q1 の直後に **Multiplexer スレッド**を挿入し、1 フレームを Qa・Qb 両方に複製して put する。

```
Q1 → Multiplexer → Qa → VolumeStage
                 → Qb → VadStage
```

Multiplexer 自身も `threading.Event` で個別停止する。

### PipelineStage 共通インターフェース

停止には 2 つの機構を用途で使い分ける。

**`threading.Event` — 会議停止・subsystem再読み込み時のstage停止**

通常の会議終了では、`SttPipeline`は共有queueへsentinelを注入せず、`Pipeline.stop(..., inject_sentinels=False)`で各stageの`threading.Event`を設定して待機を解除する。共有`stt_queue`は`AudioPipeline`が所有するため、STT側からsentinelを入れてはならない。設定再読み込みでsubsystemを停止する場合も、各stageは下流のsentinelを待ち続けず個別に停止できる。

```python
class PipelineStage(ABC):
    _stop_event: threading.Event
    _thread: threading.Thread

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join()
```

run ループは `queue.get(timeout)` + `stop_event.is_set()` の組み合わせ:

```python
def _run(self) -> None:
    while not self._stop_event.is_set():
        try:
            frame = self.input_q.get(timeout=0.5)
        except queue.Empty:
            continue
        if frame is None:
            break        # sentinel による終了も受け付ける
        # フレーム処理...
```

**sentinel `None` — AudioPipelineのEOF・全体シャットダウン**

sentinel伝播は通常の会議停止には使わない。音声入力のEOFまたは`AudioPipeline`全体のシャットダウンで`None`をQ1へ入れ、MultiplexerがQa・Qbへ転送することで、所有する下流全体へ終了を伝える。

```
CaptureStage → None → Q1
    Multiplexer: None → Qa(None) + Qb(None)
        VolumeStage: None → 終了
        VadStage:    None → Q2(None) → SttStage: None → ... → 終了
```

### ステージ一覧

| ステージ | クラス | 役割 | 入力 | 出力 |
|---------|--------|------|------|------|
| 音声取得 | `CaptureStage` | soundcard → PCM フレーム | — | Q1 (`AudioFrame`) |
| 音量計算 | `VolumeStage` | Q1 をタップし RMS 計算 | Q1 (read-only tap) | WebSocket |
| VAD | `VadStage` | 各フレームに `is_speech` を付与して全量通過 | Q1 | Q2 (`AudioFrame`) |
| 音声認識 | `WhisperStage` / `VoskStage` / `DeepgramStage` / `RemoteStage` | `is_speech` を見てバッファリング・テキスト変換 | Q2 | Q3 |
| 話者分離 | `DiarizationStage` | 話者 ID 付与 (Optional) | Q3 | Q4 |
| 出力 | (パイプライン末端) | WebSocket ブロードキャスト | Q4 or Q3 | WebSocket |

### ファイル構成

```
app/stt/
├── __init__.py
├── pipeline.py          # SttPipeline: ステージを組み立て・起動・停止する統括クラス
├── stage_base.py        # PipelineStage ABC
├── stages/
│   ├── capture.py       # CaptureStage
│   ├── multiplexer.py   # Multiplexer (Q1 ファンアウト)
│   ├── volume.py        # VolumeStage
│   ├── vad.py           # VadStage (WebRTC VAD / Silero 切り替え可)
│   ├── stt_whisper.py   # WhisperStage
│   ├── stt_vosk.py      # VoskStage
│   ├── stt_deepgram.py  # DeepgramStage
│   ├── stt_remote.py    # RemoteStage
│   └── diarization.py   # DiarizationStage
├── factory.py           # build_pipeline(cfg, role, broadcast_fn) → SttPipeline
└── audio_source.py      # AudioSource protocol, SoundcardSource (変更なし)
```

### 音声設定変更時の再読み込み

無停止のstage hot-swapは行わない。会議中はfrontendで音声device、VAD、STT設定をロックし、`AppState`がactiveの間にconfig変更通知を受けても、`SttController`は現在のaudio runtimeを維持して再読み込みを保留する。会議ライフサイクルの完了後、または会議停止中の保存時に、`AppState`の共通asyncio lifecycle mutexの下でSTTとaudio monitor pairを含む音声subsystem全体を新設定から再生成する。

再読み込みはtransactionalに行う。replacement pairの両方が起動するまでは旧pipeline pairとbackendをrollback可能な状態で保持し、片方だけ起動したreplacementは失敗時に停止して旧pair/backendを復元する。完全なpairが起動した場合だけ新runtimeをcommitする。

このreloadはTauri、FastAPI、frontend processを終了しない。一方、旧queue、VAD recurrent state、進行中STT segmentは新runtimeへ引き継がない。stage単位の差し替えやqueue handoffは行わず、rollbackにも一時的な音声subsystem停止を伴う。

### VolumeStage と AudioLevelMonitor の統合

現状の `AudioLevelMonitor`（`app/services/audio_level_monitor.py`）は STT とは独立した soundcard 接続を持っている。

新設計では `VolumeStage` が Multiplexer 経由で同一フレームを受け取るため:
- soundcard の二重オープンがなくなる
- STT が停止中でも `CaptureStage` + `Multiplexer` + `VolumeStage` だけを起動すれば音量モニタリングができる
- `AudioLevelMonitor` は廃止できる

セットアップ画面（会議開始前）での音量表示:

```mermaid
flowchart LR
    CAP[CaptureStage] --> Q1[Q1]
    Q1 --> MUX[Multiplexer]
    MUX --> Qa[Qa]
    Qa --> VOL[VolumeStage]
    VOL --> WS[WebSocket\n音量レベル]
    MUX --> Qb[Qb\n※ VAD 以降は起動しない]
    style Qb stroke-dasharray: 5 5
```

### VAD の設計

`VadStage` は選択されたエンジンをコンストラクタで受け取る。
**フレームは保留・フィルタせず、1入力につき1出力として`is_speech`を付与する**。各STTバックエンドが`is_speech`の遷移を見て、プリロールバッファ・終末バッファ・KeepAlive・Finalize等を制御する。

Sileroは512 sampleの最初の完全なwindowがthreshold以上なら直ちに発話開始とする。threshold未満が約100ms（4 window）続いた場合に発話終了とする。WhisperとVoskは直前150msをプリロールとしてsegment先頭へ追加する。

```python
@dataclass
class AudioFrame:
    pcm: bytes
    is_speech: bool
    timestamp_ms: float

class VadEngine(Protocol):
    def is_speech(self, frame: bytes, sample_rate: int) -> bool: ...

class WebRtcVadEngine: ...  # webrtcvad、最小CPU負荷
class SileroVadEngine: ...  # ONNX Runtime + 同梱int8 ONNX、既定
```

Silero VADはTorchやsherpa-onnxを依存にせず、k2-fsaが配布する16kHz専用int8 ONNX model（約208KB）をONNX Runtimeで直接実行する。modelはSHA-256を固定して配布物へ同梱し、起動時に検証する。ONNXの確率・recurrent stateは個数、shape、dtype、有限値を検証し、不正な出力ではstateをresetしてfail closedにする。

**各バックエンドの `is_speech` 活用例**

- `WhisperStage` / `VoskStage`: `False→True`でプリロールバッファ（直前150ms）を先頭に追加してsegmentを開始する。プリロールは音声gateのvoiced frame数には含めない。
- `DeepgramStage` / `OpenAIStage` / `XaiStage`: backend固有のプリロールとFinalizeを制御する。
- `ManagedSttStage` / `RemoteStage`: server側のsegment仕様に従い全frameを送る。

### キューの満杯ポリシー

キューはステージ間の受け渡しバッファ。**発話区間の蓄積など処理ロジックに必要なバッファは各ステージが内部に持つ**。キューのサイズとは独立して設計してよい。

キューが満杯のとき、ブロックせず**古いフレームを捨てて最新を入れる**。リアルタイム処理では積み残しより鮮度を優先する。

```python
def put_latest(q: queue.Queue, item) -> None:
    try:
        q.put_nowait(item)
    except queue.Full:
        q.get_nowait()   # 古い1件を捨てる
        q.put_nowait(item)
```

前提: キューの producer は1スレッドのみ。`get_nowait` と次の `put_nowait` の間に別 producer が割り込まないため、2回目の `put_nowait` が `Full` になることはない。

**キューごとの maxsize と用途**

| キュー | maxsize (目安) | 理由 |
|--------|--------------|------|
| Q1 (CaptureStage → Multiplexer) | 50 | フレーム連続性が必要。大きすぎると遅延が増える |
| Qa (Multiplexer → VolumeStage) | 50 | 古いフレームは `put_latest` で捨てるので大きさは重要でない |
| Qb (Multiplexer → VadStage) | 50 | VAD は連続フレームが必要。ドロップすると誤検出 |
| Q2 (VadStage → SttStage) | 200 | Whisper は推論に時間がかかるためバッファが必要 |
| Q3 (SttStage → DiarizationStage) | 10 | テキスト結果は小さい。溜まるなら処理が遅すぎる |

### SttConfig との関係

`SttConfig`（`app/core/config.py` に移動）が `backend`, `vad_aggressiveness`, `silence_duration` 等を保持する。`build_pipeline()` がこれを読んで適切なステージ実装を組み立てる。

## 結果

**ポジティブ**
- 新しい STT バックエンドの追加 → `stt/stages/stt_xxx.py` を1ファイル追加するだけ
- 新しい VAD エンジンの追加 → `VadEngine` Protocol を実装するだけ
- 音量モニタリングと STT が同一の soundcard 接続を共有するため、デバイス管理が単純化される
- 各ステージが独立してテスト可能（キューを与えてステージ単体を動かせる）
- 話者分離を「ステージを追加するだけ」で組み込める

**トレードオフ**
- `stt_v2/` の既存実装（`WhisperSttStream` 等）をステージベースに書き直す必要がある
- パイプラインのステージ数が増えるとデバッグ時にどのステージで詰まっているか追いにくくなる
  → 各ステージに structured logging（stage_name, queue_size 等）を追加することで対処する
- `VadStage` がフレームを全量通過させるため、Q2 のスループットは Q1 と同じ。フレームレートが高い場合（10ms/frame 等）はキューのバックプレッシャーに注意が必要
