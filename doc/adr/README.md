# Architecture Decision Records

ADRは、architecture boundary、protocol、永続schema、security/privacy/distributionなど、後から戻す費用が高い公開判断と理由を記録する。実装taskと進捗は[GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)へ置く。

## Accepted authorities

| ADR | Status | Authority |
| --- | --- | --- |
| [ADR-001](./001-python-server-directory-structure.md) | Accepted | Python server directory structure |
| [ADR-002](./002-stt-pipeline-architecture.md) | Accepted | STT pipeline architecture |
| [ADR-003](./003-meeting-recording-history-architecture.md) | Accepted | meeting recording/history architecture |
| [ADR-009](./009-live-reply-llm-usecase-runtime-provider-architecture.md) | Accepted | use-case / runtime / provider-model / config-secret boundary |
| [ADR-010](./010-ai-route-strategy.md) | Accepted | route strategy、Codex direct、generic ACP、hosted fail-closed、distribution gate |
| [ADR-011](./011-general-route-card-visibility.md) | Accepted | general route-card visibilityとAdvanced configuration boundary |
| [ADR-012](./012-native-window-chrome-and-pin-preference.md) | Accepted | native window chrome、close policy、always-on-top preference |
| [ADR-013](./013-contextual-api-credential-controls.md) | Accepted | provider-specific credential controls at points of use |
| [ADR-015](./015-localized-ui-message-contract.md) | Accepted | frontend-owned localizationとlocale-neutral `UiMessage` protocol |

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
