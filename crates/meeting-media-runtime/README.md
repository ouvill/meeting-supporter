# meeting-media-runtime

音声取得・推論 worker の起動、停止、PCM の中継を所有する Rust ライブラリです。
録音は音声取得 worker で継続し、推論が失敗しても録音の停止・保存を操作できます。
Python には制御応答・音量・確定した文字起こしのみを返します。

## 既存画面から起動

現在の音声取得は Linux の PulseAudio / PipeWire に対応しています。
リポジトリのルートで実行します。

```bash
cargo build --release --locked --manifest-path crates/meeting-media-runtime/Cargo.toml
cargo build --release --locked --manifest-path crates/meeting-audio-runtime/Cargo.toml

export MEETING_MEDIA_RUNTIME=rust
export MEETING_MEDIA_WORKER="$PWD/crates/meeting-media-runtime/target/release/meeting-media-runtime"
export MEETING_AUDIO_WORKER="$PWD/crates/meeting-audio-runtime/target/release/meeting-audio-runtime"
export MEETING_REAZON_RUNTIME=rust
export MEETING_REAZON_WORKER="$PWD/test/rust-native-backend/target/release/meeting-native-backend"
export MEETING_REAZON_MODEL="$PWD/test/rust-native-backend/target/models/reazonspeech"
export MEETING_REAZON_PUNCTUATION="$PWD/test/rust-native-backend/target/models/punctuation-bert"

npm run tauri -- dev
```

音声認識の設定は ReazonSpeech、Silero、16 kHz を使用します。
推論 worker のビルドとモデル取得は
[音声バックエンドの手順](../../test/rust-native-backend/README.md)を参照してください。
句読点を使わない場合は `MEETING_REAZON_PUNCTUATION` を未設定にします。

`MEETING_MEDIA_RUNTIME=rust` は、既存の音声取得・推論間の Python PCM 中継を置き換えます。
従来の `MEETING_AUDIO_RUNTIME=rust` の指定は不要です。
未指定時は従来の中継方式を維持します。

[会議管理](../meeting-session/README.md)・[履歴保存](../meeting-storage/README.md)の
Rust 選択設定と併用できます。設定に応じた処理を既存画面から利用できます。

## 所有権と障害の分離

入力元ごとに `Supervisor` が音声取得 worker と推論 worker を所有します。
モデルは「音声認識を準備」の操作で読み込みます。
推論は `Prepared` を消費して `Running` に遷移し、停止時は入力を切り離して
残る音声と末尾の認識を処理した後、準備済み状態に戻ります。

- 音声取得と WAV 書込みは、モデル準備・推論とは別のプロセスです。
- PCM キューは 200 フレームで制限します。あふれや音声の連番欠落を検知した場合は推論を失敗扱いにします。
- 認識結果は世代とサンプル位置を検証し、古い結果や順序の不整合を拒否します。
- 停止応答より前に末尾の認識結果を配信します。Python 側も会話処理への引渡しを待って停止を完了します。
- モデル準備は中断できます。推論の停止・再準備は録音プロセスを終了しません。
- 音声取得の通信異常時は取得 worker を終了し、成功した録音として扱いません。
- Linux では親終了通知を設定し、制御プロセスの突然の終了でも子プロセスを残しません。
- 子プロセスには用途に必要な環境変数だけを渡します。標準エラーや音声内容をログへ転送しません。

## 接続と上限

ライブラリは Tauri から直接呼べます。現段階では既存 Python バックエンドに接続する
JSONL アダプターも提供しています。これは最終配布の Python AI worker とは別の、
段階移行用の接続です。

制御要求は 16 KiB、応答は 64 KiB、制御キューは 16 件、
出力キューは 128 件までです。音量通知は約 120 ms ごとに送り、混雑時は省略します。
推論要求は最大 30 秒、モデル準備は最大 120 秒、停止時の音声処理待ちは最大 40 秒です。
失敗した処理を自動再送しません。

既存画面との WebSocket 接続、デバイス選択と準備の指示、文字起こし結果の会話・AI への引渡しは
まだ Python 側です。通常起動時の FastAPI と Python 環境準備も残ります。
worker の一般配布への組込みは未実装です。

## 検証

```bash
cargo clippy --locked --manifest-path crates/meeting-media-runtime/Cargo.toml --all-targets -- -D warnings
MEETING_MEDIA_WORKER="$PWD/crates/meeting-media-runtime/target/release/meeting-media-runtime" \
  uv run --directory python pytest -q tests/app/audio/test_media_runtime.py
```

合成 worker で末尾の結果、再開、推論障害時の録音継続、モデル準備の中断、
連番欠落、不正なパケット長、終了処理、既存 Python アダプターを検証します。

実際の worker・モデルと隔離した仮想音声デバイスでも検証できます。
上記の worker・モデル環境変数を指定し、PulseAudio と `paplay` を用意してください。

```bash
MEETING_TEST_RUST_MEDIA=1 uv run --directory python pytest -q \
  tests/app/audio/test_media_runtime.py::RealMediaIntegrationTest
```

このテストは一時的な PulseAudio サーバーに合成音を流します。
利用者のマイクや会議音声は取得しません。物理デバイスの実機試験とは分けています。
