# 共通 Python worker

Rust が必要時だけ起動する `meeting-python-worker` です。
配布する Python 実行環境は一組とし、サブコマンドで用途を選びます。
現在のコマンドは `convert-document` のみで、MarkItDown 0.1.8 による DOCX 変換を担当します。
将来の Python 専用 AI 処理も同じ実行ファイルに追加し、選択された処理だけを遅延 import します。
FastAPI、会議管理、設定・履歴の保存は含めません。

## 実行と配布

ビルド環境には Node.js と uv が必要です。Python 3.12.12 と依存は lockfile で固定します。

```bash
npm run build:python-worker
npm run dev:rust
```

生成先は `generated/python-worker/dist/meeting-python-worker/` です。
このディレクトリ内の `meeting-python-worker`（Windows は `.exe`）を Rust が直接起動します。
別の配置を使う場合は `MEETING_PYTHON_WORKER` に実行ファイルの絶対パスを指定します。
worker が未配置でも起動・音声処理・Markdown/TXT の取り込みは利用できます。
DOCX の変換に失敗した場合は失敗状態を保存し、変換できた資料だけで会議を続けます。

PyInstaller 6.22.3 の onedir 形式で、Python・native library・package data をまとめます。
各 OS・CPU 向けの環境でビルドし、ディレクトリ全体を配布してください。
release 配置は Tauri resource 内の `python-worker/` を想定しています。
Linux / Windows の標準 Tauri ビルドでは、このディレクトリ全体を音声 worker とともに同梱します。
手順と検証範囲は [Rust のインストーラー](../doc/development/rust-desktop-backend.md#linux--windows-のインストーラー)を参照してください。
macOS の配布検証は別途必要です。

利用者の Python・uv は呼ばず、起動時に package の取得・同期もしません。
DOCX 変換の一回の操作につき一プロセスを起動し、複数資料をまとめて処理して終了します。
同時実行時にはプロセスごとのメモリが必要ですが、ディスク上の Python 配布物は重複しません。
MarkItDown の基本依存には形式判定用の Magika・ONNX Runtime 等も含まれます。
`[all]` extras、画像説明用 LLM、音声認識ライブラリ、外部 plugins は有効にしません。
PDF・PPTX・XLSX の受付拡大は今回の変更に含みません。

## 呼び出し契約

Rust が検証済みバイト列を私有一時ディレクトリのファイルへ保存し、
`meeting-python-worker convert-document` の stdin に JSON を一つ書いて閉じます。
worker は stdout に JSON を一つ返して終了します。シェルは介在しません。
API キーやユーザーの Python 設定を環境変数から引き継がず、出力・例外本文をログへ記録しません。

要求の例です。実際のパスは Rust が生成し、画面からパスを受け取りません。

```json
{
  "protocol": 1,
  "id": "synthetic-request",
  "files": [{ "id": 0, "input_path": "/temporary-private-directory/0.docx" }]
}
```

応答は要求 ID と資料ごとの成功・失敗を返します。

```json
{
  "protocol": 1,
  "id": "synthetic-request",
  "results": [
    { "id": 0, "status": "converted", "markdown": "# 合成資料" }
  ]
}
```

資料単位の失敗は `{"id":0,"status":"failed","error":"conversion_failed"}` です。
不正な要求・worker 全体の失敗は非ゼロ終了とし、部分的なプロトコル出力を返しません。
Rust は version・要求 ID・結果件数・資料 ID の対応と重複を検証します。
不正な応答、起動失敗、非ゼロ終了、時間超過では対象 DOCX を失敗として扱います。

- 入力は最大 10 件、各 10 MiB、合計 20 MiB、変換結果は各 40,000 文字。
- 制御 JSON は 64 KiB、応答 JSON は 3 MiB、変換全体の期限は 60 秒。
- ZIP は最大 4,096 entries、展開後合計 64 MiB、各 XML 16 MiB・深さ 256。
- DTD・外部実体・重複 ZIP entries を拒否し、archive をディスクへ展開しません。
- 時間超過やアプリ終了時は Rust が子プロセスを終了・回収してから一時ファイルを消します。
- 変換はローカルで実行します。返答 AI に変換本文を渡す際の送信先は既存の経路設定に従います。

OS のセキュリティサンドボックスではありません。依存ライブラリの更新と、
将来サブコマンドを追加するときの入力・停止・権限境界の検証は引き続き必要です。

## 検証

合成 DOCX と一時ディレクトリを使用し、実ユーザーの資料・会議・認証情報は読みません。

```bash
npm run test:python-worker
uv run --locked --project python-worker ruff check python-worker
uv run --locked --project python-worker ruff format --check python-worker
```

Linux で凍結済み bundle も検証する場合のコマンドです。
Python のテストは bundle を空白・日本語を含むパスへ移動し、PATH を空にして実行します。
Rust の追加テストは既存画面の要求から DOCX 変換・保存・模擬 AI への反映まで確認します。

```bash
MEETING_TEST_PYTHON_WORKER="$PWD/generated/python-worker/dist/meeting-python-worker/meeting-python-worker" npm run test:python-worker
MEETING_TEST_PYTHON_WORKER="$PWD/generated/python-worker/dist/meeting-python-worker/meeting-python-worker" cargo test --locked --manifest-path crates/meeting-desktop-runtime/Cargo.toml --features test-fixtures --test direct frozen_markitdown -- --ignored
```

依存変更時は `npm run licenses:generate` で notices を生成してから bundle を再ビルドします。
Python 本体と PyInstaller bootloader も版・ライセンス本文の digest を固定して通知に含めます。
