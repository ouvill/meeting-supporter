# meeting-audio-runtime

Linux 用の音声取得・録音 worker です。ONNX と音声認識モデルをリンクせず、
PulseAudio / PipeWire の入力を 16 kHz mono PCM16LE で読みます。
会議状態・履歴データベース・モデル設定は所有しません。

## 実行と接続

[既存アプリから使う手順](../../doc/development/rust-local-speech.md)を参照してください。
ビルドには `libpulse-dev`、実行には PulseAudio 互換サーバーと共有ライブラリが必要です。
他 OS の adapter は未実装です。

- `--list-devices` はデバイス名を安定した選択 ID として返します。
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
- WAV は新規作成のみで、Unix では mode 0600 です。書き込み・確定失敗では成功結果を返しません。
- 音声取得 queue の上限は 200 フレーム、stdout queue は 64 packet です。
  stdout の混雑は録音を止めず連番の欠落として伝わります。取得 queue の超過は取得失敗です。

## 検証

`cargo test --locked` で録音内容・上書き拒否・IPC を確認します。
アプリ側のテストには異常プロトコル・停止不能時の回収・独立した PulseAudio サーバーでの
取得、音量、録音の検証があります。実ユーザーの音声はテストに使いません。
