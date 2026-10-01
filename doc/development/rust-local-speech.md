# Rust 音声認識の既存アプリへの接続

既存の会議画面・入力選択・録音・履歴・AI 連携を保ち、ローカル音声認識を
Rust の Silero / ReazonSpeech / 任意の句読点処理へ置換する開発用の経路です。
既定では音声取得と会議管理を Python が担当します。Linux では取得・録音も Rust へ切り替えられます。
通常起動から Python を除去する段階ではありません。

## 起動経路と Python 配布方針

現在の既存画面の起動では、会議管理などを担当する Python バックエンドを準備・起動します。
音声取得・認識を Rust に切り替えても、ログには `uv sync` と FastAPI の起動が表示されます。
これは Rust worker の使用有無とは別です。

会議管理の Rust 移行後は、Python が不可欠な機能だけを
PyInstaller `--onedir` で梱包し、Rust から必要時に直接起動します。
uv は開発・ビルド用とし、製品の通常起動で環境同期を行いません。
この配布方式と Rust → Python の通信契約は
[ADR-016 の Python worker 配布方針](../adr/016-rust-runtime-and-ownership-boundaries.md#python-worker-は-pyinstaller-の-onedir-形式で配布する)
に記載しています。Python 専用 worker の梱包と、現在の uv 起動経路の除去は未実装です。

## 既存画面で Rust 音声認識を使う

リポジトリルートで実行します。先に
[Rust 音声バックエンドの手順](../../test/rust-native-backend/README.md)でモデルとライブラリを準備してください。
以下のビルド例は Linux x86_64 用です。

```bash
export SHERPA_ONNX_LIB_DIR="$PWD/test/rust-native-backend/target/assets/sherpa-onnx-v1.13.8-linux-x64-shared-lib/lib"
cargo build --release --locked --features reazonspeech \
  --manifest-path test/rust-native-backend/Cargo.toml

export MEETING_REAZON_RUNTIME=rust
export MEETING_REAZON_WORKER="$PWD/test/rust-native-backend/target/release/meeting-native-backend"
export MEETING_REAZON_MODEL="$PWD/test/rust-native-backend/target/models/reazonspeech"
export MEETING_REAZON_PUNCTUATION="$PWD/test/rust-native-backend/target/models/punctuation-bert"
npm run tauri -- dev
```

いつもの設定画面で ReazonSpeech と Silero を選び、入力デバイスを設定し、
音声認識の準備、会議開始、停止を行います。認識結果は既存の会話処理へ渡され、
文字起こし表示、履歴保存、設定に応じた AI 処理につながります。
クラウド AI を有効にしている場合、その既存設定に従って認識テキストが送信されます。

`MEETING_REAZON_WORKER` は絶対パスで指定します。画面から実行ファイルは指定しません。
`MEETING_REAZON_MODEL` を省略すると、既存画面から取得する Hugging Face キャッシュを使います。
指定時はモデル状態の API も同じフォルダーを確認します。
`MEETING_REAZON_PUNCTUATION` を省略すると句読点処理を無効にします。
句読点処理を指定した場合は、モデルがない状態で勝手に無効化しません。
推論中の句読点処理だけの失敗では、通知して認識原文を使用します。

`MEETING_REAZON_RUNTIME` を解除してアプリを再起動すると従来の Python 実装に戻ります。
Rust worker の失敗時に自動で Python 推論へ切り替える処理はありません。

## 音声取得・録音も Rust に切り替える（Linux）

PulseAudio、または PipeWire の PulseAudio 互換サーバーがある Linux で試せます。
既存の入力選択・音量メーター・会議録音の操作は共通です。
マイクと相手音声のモニター入力を Rust から列挙し、既存画面へ返します。
既定の相手音声モニターが見つからない場合、マイクへ自動的に置き換えません。

開発環境では `libpulse-dev`、`libasound2-dev` と Rust が必要です。上記の音声認識用環境変数に加えて指定します。

```bash
cargo build --release --locked --manifest-path crates/meeting-audio-runtime/Cargo.toml
export MEETING_AUDIO_RUNTIME=rust
export MEETING_AUDIO_WORKER="$PWD/crates/meeting-audio-runtime/target/release/meeting-audio-runtime"
npm run tauri -- dev
```

`MEETING_AUDIO_RUNTIME` を省略すると従来の Python 音声取得・録音を使います。
Rust 取得は現在、Rust ReazonSpeech・16 kHz 設定との組み合わせに限定しています。
取得 worker は CPAL を使い、Linux に加えて Windows の WASAPI に対応します。
この Python 併用経路の手順は Linux 用です。Windows の実機検証、macOS の取得、一般配布は未対応です。
Linux の実行環境には `libpulse` / `libasound` が必要ですが、Python の soundcard はこの経路で使用しません。

Rust の取得 worker が同じ PCM を WAV 保存と音声認識向けの転送に分岐します。
取得 worker は ONNX / ReazonSpeech をリンクせず、推論 worker が異常終了しても録音を継続します。
録音先は既存の会議サービスが決め、保存完了の結果を既存の録音資産・履歴へ登録します。
WAV 書き込みエラーや取得失敗を、成功した録音として登録しません。

取得 worker からの PCM は上限付きの長さヘッダーとバイナリデータで転送します。
推論への Python adapter は現段階では PCM を既存の JSONL protocol 2 へ変換します。
転送・認識キューの欠落はフレーム連番で検出し、文字起こしを停止して通知します。
取得から WAV 保存へのキューがあふれた場合は取得失敗として扱います。
会議管理・設定・保存データベース・AI 制御には Python が残ります。

取得 worker のテストはマイク権限や実会議の音声を使いません。
以下は一時的な PulseAudio サーバーと null sink に合成音を流す結合テストです。
開発環境に `pulseaudio` と `pulseaudio-utils` が必要です。

```bash
MEETING_AUDIO_WORKER="$PWD/crates/meeting-audio-runtime/target/release/meeting-audio-runtime" \
MEETING_TEST_RUST_AUDIO=1 \
  uv run --directory python pytest tests/app/audio/test_native_audio.py -q
```

## Python 音声取得を使う場合の所有者と制約

- Python が音声取得・録音・会議状態・保存・AI を所有します。
  Rust worker はデバイスを開かず、16 kHz mono PCM16LE の 30 ms フレームを受け取ります。
- Python の VAD / ReazonSpeech stage を通さず、Rust 側で発話区間と認識を処理します。
  VAD 感度、無音時間、最低発話時間・比率・音量は既存設定から渡します。
- 入力元 `self` / `other` ごとに worker を起動します。準備したモデルは会議間で保持します。
  モデルが二重ロードされるメモリコストがあり、共有 supervisor への統合は今後の対象です。
- 会議開始時に準備中の音声を捨て、停止時に両入力の境界を固定します。
  受理済み音声と最後の発話を処理し、既存の会話処理への受け渡し完了を待ってから会議を閉じます。
- 準備は 120 秒、各要求は 30 秒、停止時の排出は入力ごとに 10 秒を上限とします。
  異常終了・不正応答・期限超過では worker を kill / wait し、エラーを通知します。
  強制停止では未確定の音声を失う可能性があります。
- 暫定 IPC は上限付き JSONL protocol 2 です。要求 ID と source 世代を照合します。
  binary PCM 転送と取得段階からの連番・欠落通知は音声取得移行時の課題です。
  既存の入力キューが過負荷時に古いフレームを捨てる制約は残っています。
- 既存履歴には表示用の確定テキストを渡します。認識原文と句読点適用後テキストの別保存、
  話者推定・途中認識結果・一般配布用の配置は今回の範囲に含みません。

この橋渡しは音声取得・会議管理の Rust 移行に合わせて除去します。
公開 HTTP / WebSocket 契約と既存の React 画面は変更していません。
[ADR-016](../adr/016-rust-runtime-and-ownership-boundaries.md) は引き続き Proposed です。

## 独立した音声検証画面

`native-speech` feature と `npm run dev:native` は、Python を起動せずに
マイク取得と worker 管理を検証する専用画面です。既存アプリの置換先ではありません。
以下はこの検証画面だけの操作・制約です。

### 検証画面を起動する

以下はリポジトリルートから実行します。モデルは
[Rust 音声バックエンドの手順](../../test/rust-native-backend/README.md)で
ReazonSpeech と必要に応じた句読点モデルを準備してください。
Tauri の通常の OS ビルド依存も必要です。

Linux x86_64 で音声 worker をビルドします。

```bash
export SHERPA_ONNX_LIB_DIR="$PWD/test/rust-native-backend/target/assets/sherpa-onnx-v1.13.8-linux-x64-shared-lib/lib"
cargo build --release --locked --features reazonspeech \
  --manifest-path test/rust-native-backend/Cargo.toml
```

Python リソースを準備しない設定を指定して Tauri を起動します。

```bash
npm run dev:native
```

画面で「文字起こしを開始」を押すと、モデル準備後にシステムの既定マイクから取得します。
「停止」はモデル準備中にも使えます。取得を止めた後に受理済み音声を処理し、
最後の発話を受け取って worker を終了します。
正常停止後は、表示内容を保持したまま再開できます。
句読点を適用した結果と認識原文は別に保持します。

文字起こしはメモリ内だけに保持し、アプリを閉じると失われます。
「テキストを保存」で保存先を選び、確定済みのテキストを書き出せます。
音声はクラウドへ送信せず、音声ファイルも保存しません。

開発ビルドでは `test/rust-native-backend/target/release/` の worker と
`target/models/` のモデルを初期値にします。モデルフォルダーは画面から変更できます。
`MEETING_NATIVE_WORKER` は開発ビルドだけで実行ファイルを指定するための環境変数です。
画面から任意の実行ファイルを指定する機能はありません。

### 検証画面の所有権と失敗時の扱い

- `crates/meeting-speech-runtime` が session 世代、phase、文字起こし、子プロセスを所有します。
- 画面は Tauri command で操作し、400 ms ごとに snapshot を取得します。
  revision が古い応答は適用しません。画面再読み込みでは取得を勝手に再開せず、
  Rust が所有する進行中の状態を再表示します。
- 同時開始を拒否し、旧 worker の終了前に新しい worker を起動しません。
- モデル準備は 120 秒、取得・推論の進捗停止は 30 秒、停止処理は 5 秒で期限切れにします。
  期限切れの worker は kill / wait します。強制停止では末尾の未確定音声を失う可能性を表示します。
- worker は他のプロセスを起動しません。標準入力の stop / EOF で取得を止め、
  標準出力では protocol version、開始、進捗、確定結果、停止、エラーを返します。
  進捗は推論を実行する thread から返します。
- 標準出力の一行と受信 queue に上限を設け、破損データや過剰出力では worker を停止します。
  native stderr は UI に転送しません。
- 文字起こしは最大 2,000 件まで保持し、上限に達したら停止します。
  表示済みの内容を黙って削除せず、保存・消去して再開する案内を表示します。

### 検証画面の範囲

このモードは独立した検証用です。
会議履歴・録音保存・システム音声・話者分離・AI 支援・モデル自動取得は未接続です。
モデルの未導入時でも画面と設定操作は利用でき、文字起こし開始時にエラーを表示します。

最初の接続では、マイク取得も推論 worker 内にあります。
worker 障害時も録音を継続できる構成ではありません。
録音機能をつなぐ前に capture と recording を推論から分離します。
[ADR-016](../adr/016-rust-runtime-and-ownership-boundaries.md) は引き続き Proposed です。

`tauri.native.conf.json` は Python リソースの準備と bundle を無効にしています。
署名済みの一般配布パッケージや他 OS の動作確認は含みません。
release 版へ展開する場合は、worker と共有ライブラリを resource の `native/` へ、
モデルを app-data の `models/` へ配置する配布処理とライセンス通知が別途必要です。

### 検証

```bash
cargo test --locked --manifest-path crates/meeting-speech-runtime/Cargo.toml
cargo test --locked --manifest-path src-tauri/Cargo.toml --features native-speech
npm test -- src/components/native/NativeSpeechScreen.test.tsx \
  src/__tests__/App.nativeSpeech.test.tsx
npm run build
```

supervisor は合成結果を返すテスト worker で、二重開始、停止中の flush、
再開時の世代、異常終了、不正 protocol、停止期限切れ、モデルエラーを検証します。
フロントエンドは Python API hook を mount しないこと、準備中の停止、
原文表示、句読点の無効化、マイクエラーを検証します。
実モデルの認識・句読点とマイク処理経路の合成音声検証は
[音声バックエンドの検証手順](../../test/rust-native-backend/README.md)に分けています。


### Tauri ウィンドウでの結合テスト

Linux の Xvfb / D-Bus 環境で、合成結果を返す worker を用いて検証します。
Python 環境や実マイクは使いません。

```bash
npm run tauri -- build --debug --no-bundle --features native-speech,webdriver \
  --config src-tauri/tauri.native.wdio.conf.json
xvfb-run -a dbus-run-session -- node scripts/run-tauri-wdio.mjs \
  test/tauri/native.wdio.conf.ts
```

開始・停止、原文表示、画面再読み込み後の状態復元、再開、テキスト保存、消去を確認します。
実モデルの認識とは分けて検証しており、物理マイクの実機試験を代替するものではありません。

## 既存画面の履歴保存を Rust に切り替える

音声入力・推論の選択とは独立して、履歴保存も Rust に切り替えられます。
[meeting-storage の起動・検証手順](../../crates/meeting-storage/README.md)を参照してください。

```bash
cargo build --release --locked --manifest-path crates/meeting-storage/Cargo.toml
export MEETING_STORAGE_RUNTIME=rust
export MEETING_STORAGE_WORKER="$PWD/crates/meeting-storage/target/release/meeting-storage"
npm run tauri -- dev
```

既存の会議サービスが Rust worker に保存を依頼し、Rust / SQLx が SQLite の接続を所有します。
SQL はビルド時に生成する検証専用DBに対してコンパイル時検証します。
この段階では会議管理と FastAPI が Python に残り、起動時の Python 環境準備も残ります。

## 既存画面の会議管理を Rust に切り替える

`MEETING_SESSION_RUNTIME=rust` で、開始・停止の順序と復旧判断を Rust に切り替えられます。
[meeting-session の起動・検証手順](../../crates/meeting-session/README.md)を参照してください。
会議管理と履歴保存の Rust worker を併用できます。

この段階では Python が Rust の指示に従って既存の音声・AI・配信処理を実行します。
Python の起動を外すには、音声 worker の制御と既存画面への接続も Rust に移す必要があります。

## 音声 worker の制御と PCM 中継を Rust に切り替える

`MEETING_MEDIA_RUNTIME=rust` で、音声取得と推論 worker の間を Rust が直接中継します。
[meeting-media-runtime の起動・検証手順](../../crates/meeting-media-runtime/README.md)を参照してください。
Python には PCM を渡さず、音量・認識結果・制御応答だけを渡します。
推論を停止しても録音を継続でき、会議管理・履歴保存の Rust 設定と併用できます。

画面との接続と AI への結果引渡しは Python 側に残っています。
Python 起動を外す次の段階では、この Rust ライブラリを Tauri から直接呼び出します。
