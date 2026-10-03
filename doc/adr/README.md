# Architecture Decision Records

ADRは、architecture boundary、protocol、永続schema、security/privacy/distributionなど、後から戻す費用が高い公開判断と理由を記録する。実装taskと進捗は[GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)へ置く。

## Accepted authorities

| ADR | Status | Authority |
| --- | --- | --- |
| [ADR-001](./001-python-server-directory-structure.md) | Superseded by ADR-021 | Python server directory structure |
| [ADR-002](./002-stt-pipeline-architecture.md) | Accepted（Python の実装構成は ADR-022 で部分置換） | STT pipeline architecture |
| [ADR-003](./003-meeting-recording-history-architecture.md) | Accepted（中断会議は ADR-018、Python の実装構成は ADR-022 で部分置換） | meeting recording/history architecture |
| [ADR-009](./009-live-reply-llm-usecase-runtime-provider-architecture.md) | Accepted（議事録生成は ADR-019、Rust 設定は ADR-020、Pydantic AI の実装構成は ADR-022 で部分置換） | use-case / runtime / provider-model / config-secret boundary |
| [ADR-010](./010-ai-route-strategy.md) | Accepted（外部接続は ADR-017、議事録生成は ADR-019、Python のローカル境界は ADR-022 で部分置換） | route strategy、Codex direct、generic ACP、hosted fail-closed、distribution gate |
| [ADR-011](./011-general-route-card-visibility.md) | Accepted（Registry 導入は ADR-017 で部分置換） | general route-card visibilityとAdvanced configuration boundary |
| [ADR-012](./012-native-window-chrome-and-pin-preference.md) | Accepted | native window chrome、close policy、always-on-top preference |
| [ADR-013](./013-contextual-api-credential-controls.md) | Accepted（音声認識の提供範囲は ADR-019 で部分置換） | provider-specific credential controls at points of use |
| [ADR-015](./015-localized-ui-message-contract.md) | Accepted（Python の producer・検証対象は ADR-022 で部分置換） | frontend-owned localizationとlocale-neutral `UiMessage` protocol |
| [ADR-017](./017-acp-registry-and-shared-rust-client.md) | Accepted | Rust の共通 ACP client、Registry 導入・認証、実行と費用の境界 |
| [ADR-018](./018-interrupted-meeting-history.md) | Accepted（Python lifecycle が残る前提は ADR-022 で部分置換） | Rust 直接接続の中断会議、起動時の整理、保存失敗と停止失敗の分離 |
| [ADR-019](./019-local-speech-and-saved-history.md) | Accepted（Rust の旧設定の扱いは ADR-020 で部分置換） | ローカル音声認識と保存済み履歴への整理 |
| [ADR-020](./020-versioned-rust-settings.md) | Accepted | Rust のバージョン付き設定形式、検証、旧設定の廃止 |
| [ADR-021](./021-retire-python-backend.md) | Accepted | 旧 Python バックエンドと移行用 adapter の撤去、MarkItDown worker の保持 |
| [ADR-022](./022-rust-backend-authority.md) | Accepted | Rust 移行後のバックエンド・検証の責務、旧 ADR の Python 前提の部分置換 |

## Proposed decisions

| ADR | Status | Review scope |
| --- | --- | --- |
| [ADR-016](./016-rust-runtime-and-ownership-boundaries.md) | Proposed | Rust 移行時の起動分離、会議状態の所有、推論 process 隔離、保存・UI 契約 |

Proposed は設計案であり、上記の Accepted authorities を置き換えない。

## Required structure

- **Status**: Proposed / Accepted / Superseded / Rejected
- **Date**: 判断日
- **Context**: 判断が必要になった背景と制約
- **Decision**: 採用する境界とpolicy
- **Rejected Alternatives**: 採用しなかった案と理由
- **Consequences**: benefit、cost、risk
- **Supersession**: 置き換える／置き換えられる公開ADR
- **Related Documents**: PRD、Product Surfaces、公開ADR、GitHub Issue

## Lifecycle

- `Proposed`: review中。実装authorityではない。
- `Accepted`: 合意・採用された公開判断。
- `Superseded`: 新しい公開ADRに置き換え済み。置換先を必須とする。
- `Rejected`: 採用しなかった提案。

Accepted ADRの判断を変更するときは本文を書き換えず、次の未使用番号でADRを作る。番号を再利用しない。

## Review gate

- PRDのrequirementとavailabilityに矛盾しない
- route、runtime、provider/model、config/secretの責務を混ぜない
- security、privacy、error boundary、migration、distributionへの影響を扱う
- hosted serviceのserver実装または運用情報を含めない
- implementation checklistと進捗をGitHub Issueへ分離する
- statusとsupersession chainをindexと双方向に更新する
