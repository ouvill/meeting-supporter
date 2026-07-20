# Documentation

`doc/`には、利用者・contributorが現在のproduct behaviorと公開architectureを理解するための正本だけを置く。同じ問いに二つのauthorityを作らない。

## Authority index

| 問い | Authority | 内容 |
| --- | --- | --- |
| なぜ作るか | [Product Vision](./product/vision.md) | problem、対象利用者、promise、原則 |
| 何を提供するか／現在利用できるか | [Product Requirements](./product/prd.md) | outcome、requirements、availability |
| どの画面状態・copyを使うか | [Product Surfaces](./ui/product-surfaces.md) | surface責務、state、一般向け文言 |
| なぜ不可逆な技術判断をしたか | [Accepted ADRs](./adr/README.md) | decision、alternatives、consequences |
| 現在どう動くか | codeとtests | 実装済みbehaviorと検証可能なcontract |
| 何を直す／追加するか | [GitHub Issues](https://github.com/ouvill/meeting-supporter/issues) | 公開可能なbug、feature、進捗 |
| security defectを報告するには | [Security Policy](../SECURITY.md) | private reportingとcoordinated disclosure |

## Authority boundaries

### Product Vision

解く問題、支援する人、価値観を扱う。feature list、availability、進捗は置かない。

### Product Requirements

利用者が何をできるか、提供状態、cross-cutting requirementを扱う。実装taskや画面copyの詳細は置かない。

### Product Surfaces

surface責務、state transition、empty/loading/error/disabled、一般向けcopyを扱う。availabilityはPRDを参照し、画面側で独自に推測しない。

### Architecture Decision Records

architecture boundary、protocol、永続schema、security/privacy/distributionなど、戻す費用が高い公開判断と理由を扱う。実装taskや進捗は置かない。

### Code and tests

現在の実装挙動はcodeとtestsを正本とする。文書が実装と矛盾した場合は、公開contractへの影響を確認して同じchangeで解消する。

### GitHub Issues

公開可能なbug、feature、実装進捗を管理する。credential、token、個人情報、meeting transcript/audio、raw stderr、絶対home pathをissue、PR、log、screenshotへ記録しない。

## Hosted service boundary

Meeting Supporterが運営するhosted serviceのserver実装・運用文書は、このOSSリポジトリに含まれない。通常のOSS buildではhosted serviceは未設定で利用できない。local STT、利用者自身のAPI credential、Ollama、Codex、ACPはhosted accountなしで利用できる。

public clientに残る認証境界、schema validation、未設定時のfail-closed behaviorは公開codeのcontractとして扱う。

## Review gates

### Product or UI change

- user outcome、availability、surface stateが矛盾しない
- `availability`、`readiness`、`selectable`をUIが推測しない
- raw exception、prompt、stderr、token、credentialを表示しない
- keyboard、focus、色以外の状態表現を確認する
- hosted service未設定時にlogin、checkout、route選択を開始できない

### Architecture change

- Context、Decision、Rejected Alternatives、Consequencesがある
- route、runtime、provider/model、config/secretの責務を混ぜない
- security、privacy、migration、distributionへの影響を扱う
- implementation checklistはGitHub Issueへ分離する

### Documentation change

- relative linkが存在する
- 同じ問いのauthorityを増やしていない
- private implementation、internal planning、運用情報を含めていない
- current behaviorはcode/tests、進捗はGitHub Issuesへ向ける
