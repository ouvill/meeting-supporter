# meeting-storage

会議履歴 SQLite を扱う Rust ライブラリです。Tauri 内の `meeting-desktop-runtime` が直接利用し、SQLx の接続を所有します。
起動方法は [Rust バックエンド](../../doc/development/rust-desktop-backend.md)を参照してください。

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
- 会議内容や SQL の内部エラーを標準エラーに出力しません。

## 検証

合成データと一時DBのみを使用します。

```bash
cargo test --locked --manifest-path crates/meeting-storage/Cargo.toml
cargo test --locked --manifest-path crates/meeting-desktop-runtime/Cargo.toml
```

旧スキーマ移行、破損データの拒否、会議の保存・再読込み、録音と履歴削除は Rust の単体・結合テストで検証します。
