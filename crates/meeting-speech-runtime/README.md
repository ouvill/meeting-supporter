# meeting-speech-runtime

Tauri に依存しないローカル文字起こしのセッション管理です。
一つの音声 worker を所有し、開始・停止・世代・確定結果・期限を管理します。
公開 API は `Runtime`、`Config`、`Snapshot` と型付きの `Error` です。

起動方法と制約は [Rust ローカル文字起こし](../../doc/development/rust-local-speech.md)、
設計方針は [ADR-016](../../doc/adr/016-rust-runtime-and-ownership-boundaries.md)を参照してください。

この crate は Meeting Supporter 本体の一部であり、
[AGPL-3.0-only](../../LICENSE) に従います。
