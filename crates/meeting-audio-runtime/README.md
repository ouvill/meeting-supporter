# meeting-audio-runtime

CPAL 0.18.2 を使う Linux / Windows / macOS 用の音声取得・録音 worker です。
ONNX と音声認識モデルをリンクせず、デバイスの入力を 16 kHz mono PCM16LE に変換します。
会議状態・履歴データベース・モデル設定は所有しません。

## 実行と接続

[既存アプリから使う手順](../../doc/development/rust-local-speech.md)を参照してください。
Linux のビルドには `libpulse-dev` と `libasound2-dev`、実行には PulseAudio 互換サーバーと `libpulse` / `libasound` が必要です。
CPAL の PulseAudio バックエンドを明示的に選び、PipeWire では `pipewire-pulse` を使います。
monitor と既定出力の対応関係は CPAL が公開していないため、デバイスの列挙情報だけは `libpulse-binding` で補います。
Windows は CPAL の WASAPI バックエンドでマイクと出力デバイスのループバックを取得します。
macOS は CoreAudio のマイク入力と process tap によるシステム音声取得を使います。
[CPAL 0.18.2 の対応条件](https://docs.rs/crate/cpal/0.18.2)に合わせ、macOS 14.6 以降が必要です。
CPAL の制約でシステム音声取得は出力専用デバイスを対象にします。
入出力一体型の USB ヘッドセットなどはマイクとして列挙し、システム音声入力には表示しません。
初回はマイク・システム音声のアクセス許可が必要で、起動応答を最大 90 秒待ちます。
権限を拒否した場合は「システム設定 → プライバシーとセキュリティ」で許可して再接続します。
Windows / macOS の実デバイス取得と権限ダイアログは実機での検証が必要です。

- `--list-devices` は安定した選択 ID を返します。Linux は既存の Pulse source 名、Windows / macOS は CPAL の device ID です。
- `--role self|other [--device ID]` は選択入力を取得します。
  `self` の既定値はマイク、`other` の既定値は既定出力の monitor です。
- stdin は最大 16 KiB の JSONL です。`id` と `command` を持ち、
  command は `start_recording`（`path` 必須）、`stop_recording`、`shutdown` を受け付けます。
- stdout は little-endian u32 の JSON 長、PCM 長、JSON、PCM の順です。
  PCM は 0 または 960 bytes、JSON は receiver 側で最大 16 KiB に制限します。
  `ready` は protocol 1、`audio` はフレーム連番と peak を持ちます。
- 不正制御・EOF で録音を確定して終了します。終了応答後の process exit も親が確認します。
  capture のネイティブ read が停止しない場合も、親が期限後にプロセス全体を終了・回収します。
- 入力フレームが 5 秒間届かなければ取得失敗として終了します。
- Windows のループバックでは同じ出力に無音ストリームを開き、無再生時にも連続した録音時間を保ちます。
- CPAL callback は入力をモノラルにして最大 5 秒分の ring buffer に渡します。
  別 thread の `rubato` による帯域制限付き変換で 16 kHz・30 ms フレームを作ります。
  callback ではメモリ確保、ファイル書き込み、推論を行いません。
- WAV は新規作成のみで、Unix では mode 0600 です。書き込み・確定失敗では成功結果を返しません。
- 音声取得 queue の上限は 200 フレーム、stdout queue は 64 packet です。
  stdout の混雑は録音を止めず連番の欠落として伝わります。取得 queue の超過は取得失敗です。

## 検証

`cargo test --locked` で録音内容・上書き拒否・IPC・変換後の音質と時間・バッファ超過を確認します。
Linux では `pulseaudio` と `pulseaudio-utils` を用意し、リポジトリルートから実行します。

```bash
cargo build --locked --manifest-path crates/meeting-audio-runtime/Cargo.toml
python3 test/audio-capture-smoke.py --worker crates/meeting-audio-runtime/target/debug/meeting-audio-runtime
```

専用サーバーの仮想マイクと monitor に合成音を送り、既定・明示デバイスの選択、音量、連番、WAV を確認します。
通常の音声サーバーや実マイクには接続しません。Windows CI は worker のビルドとデバイス不要のテストを実行します。
