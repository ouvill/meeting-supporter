# meeting-session

会議の開始・停止と失敗時の復旧判断を所有する Rust ライブラリです。
既存の画面・WebSocket API は維持し、移行期間は Python アダプターが
Rust の指示に従って音声・録音・履歴・AI・画面配信の処理を実行します。

## 起動

リポジトリのルートから実行します。履歴保存も Rust に切り替える例です。

```bash
cargo build --release --locked --manifest-path crates/meeting-session/Cargo.toml
cargo build --release --locked --manifest-path crates/meeting-storage/Cargo.toml
export MEETING_SESSION_RUNTIME=rust
export MEETING_SESSION_WORKER="$PWD/crates/meeting-session/target/release/meeting-session"
export MEETING_STORAGE_RUNTIME=rust
export MEETING_STORAGE_WORKER="$PWD/crates/meeting-storage/target/release/meeting-storage"
npm run tauri -- dev
```

音声取得・推論は既存の選択設定を使用します。
[音声バックエンドの切替手順](../../doc/development/rust-local-speech.md)と併用できます。
環境変数未指定時は Python の会議管理です。不正な選択値は起動エラーにします。

## 所有権

Rust は会議 ID、開始・終了時刻、状態、次に実行する処理、復旧方針を所有します。
Python の `current_session` は既存の AI・UI が参照する互換用のビューです。
発言や AI の内容は、この段階では Python の会話処理が管理します。

ライブラリはデバイス・DB・Tauri・Python に依存しません。
`Coordinator` にコマンドを渡すと、実行する `Effect` と状態を返します。
実行側は結果を `Outcome` として返し、Rust が次の処理を決めます。
状態と処理を enum、失敗を thiserror で表現し、不正な結果や古い応答では状態を進めません。

開始時は音声設定の反映、下書き保存、録音開始、認識開始の順に実行します。
停止時は AI 生成の取消、認識の停止と末尾の処理、再度の AI 生成取消、
録音の確定、発言保存の完了待ち、会議の完了保存、音声設定の反映の順です。

## 失敗時の動作

- 認識開始に失敗した場合は部分的に開始した音声処理を止め、録音を確定して会議を中断します。
- 録音情報を保存できない場合は録音ディレクトリを削除します。
  削除も失敗した場合は下書きとファイルを残し、会議を完了扱いにしません。
- 発言保存に失敗した場合や保存待ちが期限を超えた場合も、下書きを維持します。
- 停止の成否が不明な場合は新しい会議を開始しません。
- 接続喪失・操作のキャンセル時は音声処理と録音を停止し、worker を回収します。
  不明な処理を再送せず、アプリの再起動と履歴の確認を案内します。

## 一時的な接続

worker は stdin/stdout の JSONL を使用します。
要求 ID と会議の世代・処理番号を照合し、古い結果や二重応答を拒否します。
要求は 4 KiB、応答の読込みは 16 KiB、制御通信の待機は 5 秒です。
音声・保存などの処理には別途期限を設けます。

この worker は通常画面への段階移行用です。
Tauri からライブラリを直接呼び、音声 worker の制御を Rust に移す段階で、
Python の処理実行アダプターとこの JSONL 接続を取り除きます。
現在も FastAPI・Python の起動は必要です。一般配布への worker 同梱は未実装です。

## 検証

```bash
cargo test --locked --manifest-path crates/meeting-session/Cargo.toml
cargo clippy --locked --manifest-path crates/meeting-session/Cargo.toml --all-targets -- -D warnings
MEETING_SESSION_WORKER="$PWD/crates/meeting-session/target/release/meeting-session" \
MEETING_STORAGE_WORKER="$PWD/crates/meeting-storage/target/release/meeting-storage" \
  uv run --directory python pytest -q tests/app/meetings/test_native_lifecycle.py
```

Rust の状態遷移テストと、実 worker・一時DB・合成音声処理を使う結合テストを分けています。
物理マイク、クラウド AI、実利用の会議データは使いません。
