# ADR-022: Rust 移行後のバックエンドと検証の責務を確定する

- **Status**: Accepted
- **Date**: 2026-10-04
- **Builds on**: [ADR-021](./021-retire-python-backend.md)
- **Supersession**: ADR-002、ADR-003、ADR-009、ADR-010、ADR-015、ADR-018 の Python バックエンドを前提とする部分を置換する。対象は下記の表に限定する。

## Context

標準バックエンドは Rust に移行し、ADR-021 に従って旧 Python バックエンドを撤去した。
一方、Accepted ADR には Python のプロセス、クラス、同期方式、検証対象を現行の前提とする記述が残っている。
過去の判断を保存しながら、今後の実装と検証が従う境界を明確にする必要がある。

## Decision

### Rust が標準バックエンドを所有する

Linux、Windows、macOS の標準構成では、Tauri 内の `meeting-desktop-runtime` が
会議管理、設定、履歴、資料、AI 返答と、認証付き loopback HTTP / WebSocket API を所有する。
会議の状態遷移は `meeting-session`、永続化は `meeting-storage`、音声プロセスの管理は
`meeting-media-runtime` が担う。音声取得・推論の native worker は Rust 側が起動・停止する。

旧 Python の `AppState`、asyncio mutex、stage ごとの thread / queue、
`MeetingLifecycleCoordinator`、Pydantic AI の runtime class を再実装することは要求しない。
Rust の各責務で開始・停止、キャンセル、保存失敗、遅延した結果の分離を検証する。
中断会議と保存済み履歴の扱いは ADR-018 と ADR-019、設定と認証情報の扱いは ADR-020 に従う。

ローカル API の capability token は端末内の呼び出しを確認するためのものであり、
hosted service の利用者認証には使わない。通常の OSS build は hosted service 未設定時に fail closed とする。

### 検証対象を現在の実装へ合わせる

API / WebSocket の producer と契約は Rust、generated client と画面側の処理は TypeScript、
ウィンドウ間の動作は desktop scenario で検証する。OpenAPI は Rust の HTTP handler から生成する。
ADR-015 の locale と表示メッセージの責務は維持し、Python バックエンドの検証要件を Rust へ移す。
この決定は localization 機能の実装完了を意味しない。

Python の実行時責務は ADR-021 に従う DOCX 変換用 `python-worker/` に限定する。
この worker と、保持するモデル変換・ライセンス生成・CI 補助スクリプトはそれぞれの用途で検証する。
開発設定と検証コマンドから、削除済みの Python バックエンドを参照しない。

### 過去の ADR の適用範囲

既存 ADR の本文は判断当時の記録として保持し、冒頭と index から本 ADR へリンクする。
以下の範囲外の判断と、他の ADR による部分置換は維持する。

| ADR | 本 ADR で置き換える範囲 |
|---|---|
| [ADR-002](./002-stt-pipeline-architecture.md) | Python の stage、thread / queue、asyncio による音声処理と再読み込みの実装構成。Rust の media supervisor と native worker の境界を適用する。 |
| [ADR-003](./003-meeting-recording-history-architecture.md) | `python/app/meetings/` の構成、Python service / coordinator / EventBus、standalone Python 起動の前提。Rust の会議管理と保存ライブラリを適用する。 |
| [ADR-009](./009-live-reply-llm-usecase-runtime-provider-architecture.md) | Pydantic AI の runtime class と model inference への写像。use-case / runtime / provider / config の責務分離を Rust に適用する。 |
| [ADR-010](./010-ai-route-strategy.md) | 直接起動する Python backend と `python-server` が存在する前提。標準の Rust loopback API に capability token の境界を適用する。 |
| [ADR-015](./015-localized-ui-message-contract.md) | Python API / WebSocket producer と Python coverage の前提。表示契約を担うバックエンドの検証対象を Rust とする。 |
| [ADR-018](./018-interrupted-meeting-history.md) | Python バックエンドと移行用 adapter が対象外の別 lifecycle として残る前提。標準バックエンドは Rust のみとする。 |

## Rejected Alternatives

- 過去の ADR の本文を現在の実装へ書き換える: 判断当時の背景や採用理由が失われるため採用しない。
- 旧 Python バックエンド用の互換層や検証環境を復活させる: ADR-021 の撤去方針と矛盾するため採用しない。
- Python の参照を一括削除する: DOCX worker、開発補助ツール、過去の設計記録まで失われるため採用しない。

## Consequences

標準バックエンドの実装・検証対象が Rust に揃い、旧 runtime を前提とする追加実装を防げる。
履歴として残る ADR を読む際は部分置換のリンクを辿る必要がある。
DOCX worker と開発補助ツールの Python 依存は引き続き保守する。
この決定による保存形式、設定、認証情報、公開 API の変更はない。

## Related Documents

- [Product Requirements](../product/prd.md)
- [Product Surfaces](../ui/product-surfaces.md)
- [Rust バックエンドの直接接続](../development/rust-desktop-backend.md)
- [ADR-017: ACP Registry と共通 Rust client](./017-acp-registry-and-shared-rust-client.md)
- [ADR-019: ローカル音声認識と保存済み履歴](./019-local-speech-and-saved-history.md)
- [ADR-020: Rust の設定形式](./020-versioned-rust-settings.md)
