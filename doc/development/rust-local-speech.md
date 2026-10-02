# Rust ローカル文字起こしの検証

通常のアプリ起動は [Rust バックエンド](rust-desktop-backend.md)を参照してください。

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

検証画面用の設定を指定して Tauri を起動します。

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

この検証画面ではマイク取得も推論 worker 内にあります。通常の会議画面は、取得・録音 worker と推論 worker を分離しています。
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
フロントエンドは 通常画面の API hook を mount しないこと、準備中の停止、
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
