# meeting-storage

既存の会議履歴 SQLite を扱う Rust ライブラリと、移行期間用の worker です。
SQLx の接続を Rust が所有し、既存の Python 会議サービスは repository protocol 経由で利用します。
UI・API・履歴ファイルの形式は維持します。

## 起動

リポジトリのルートで実行します。

```bash
cargo build --release --locked --manifest-path crates/meeting-storage/Cargo.toml
export MEETING_STORAGE_RUNTIME=rust
export MEETING_STORAGE_WORKER="$PWD/crates/meeting-storage/target/release/meeting-storage"
npm run dev:python
```

未指定時は従来の Python 実装です。Rust を選択した場合、起動失敗時の自動切替は行いません。
会議管理・API はまだ Python で動くため、通常起動時の Python / uv は残ります。
worker の配布パッケージへの組込みは未実装です。

## SQL のコンパイル時検証

CRUD は `query!` / `query_as!` / `query_scalar!` で検証します。
`build.rs` が `src/schema.sql` から Cargo の `OUT_DIR` に検証専用の SQLite を生成し、
SQLx マクロに接続先を渡します。SQLx CLI、常駐DB、ユーザーの履歴DB、追跡対象のバイナリDBは不要です。
DDL と接続用 PRAGMA は SQLx の `Executor` で実行します。
スキーマ変更時は、検証用スキーマと実行時の移行処理を合わせて更新してください。
コンパイル時検証だけでは既存DBの移行互換性は保証されないため、移行テストも必要です。

## 実行境界

- WAL、外部キー制約、接続数 1、busy timeout 5 秒。録音情報の一括保存はトランザクションです。
- 既存のスキーマ v1 を v2 に移行し、未対応バージョンは拒否します。
- 状態・録音形式・役割を enum、日時を検証付き型、エラーを thiserror で表現します。
- stdin/stdout は要求 ID 付き JSONL。要求 8 MiB、応答 32 MiB、Python 側の待機期限は 10 秒です。
- 切断・期限切れ・キャンセル時は worker を終了して回収します。
  結果が不明な書込みは自動再試行しません。
- 会議内容や SQL の内部エラーを標準エラーに出力しません。
- `dev:rust` の [直接接続](../../doc/development/rust-desktop-backend.md)では、Tauri がこのライブラリを直接利用し、保存用 IPC は使いません。

## 検証

合成データと一時DBのみを使用します。

```bash
cargo clippy --locked --manifest-path crates/meeting-storage/Cargo.toml --all-targets -- -D warnings
MEETING_STORAGE_WORKER="$PWD/crates/meeting-storage/target/release/meeting-storage" \
  uv run --directory python pytest -q tests/app/meetings/test_native_repository.py
```

双方向の Python / Rust 互換性、旧スキーマ移行、制約違反時のロールバック、
削除の cascade、保存容量集計、破損日時、未対応スキーマ、
既存会議サービスの保存・再読込みを検証します。
