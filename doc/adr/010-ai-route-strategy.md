# ADR-010: AI route strategyとしてCodex直接経路と正直なavailabilityを採用する

- **Status**: Accepted
- **Date**: 2026-07-10
- **Updated**: 2026-07-19（Codex情報AIのcomplete-note検証・CAS境界を追加）
- **Builds on**: ADR-009

## Context

Meeting Supporterは未リリースであり、APIキーを準備できない利用者にも実動するAI経路を示したい。一方、実装候補、試験提供、一般提供、将来構想が同じ「provider」一覧に混在すると、通常buildで未設定のhosted serviceを選べるように見せたり、Codex App Serverとgeneric ACPを同じprotocolとして扱ったりする危険がある。

ADR-009は product use-case / runtime / provider-model / config-secret の4層を定めた。本ADRはその境界を変えず、利用者に見せるrouteと短期のruntime戦略について、配布・認証・availabilityに関わる不可逆な判断を記録する。

2026-07-10時点の開発環境では、グローバル/PATH上の公式Codexについて次を実測した。

```text
$ codex --version
codex-cli 0.144.0

$ codex login status
Logged in using ChatGPT
```

これはbinary検出とChatGPTログイン状態の証拠であり、Meeting SupporterからのE2E生成品質、対応OS、一般提供可能性の証明ではない。

## Decision

### 1. Routeをユーザー選択の単位にする

routeは「どの方法でAIを使うか」を表し、runtime、provider、model、secretとは分離する。route read modelは次を必須とする。

```text
id, kind, label, description,
availability, readiness, selectable, selected,
data_location, billing_owner, capabilities,
reason_code, message, action, service_tier
```

availability、readiness、selectableを独立させる。UIはこれらを再推測しない。`selected` は選択可能なrouteに限る。

### 2. Codexは公式App Serverへ直接接続する専用runtimeにする

短期のAPIキー不要経路として、PATH上の公式 `codex` が提供するApp ServerへPythonから直接接続する専用Codex runtimeをexperimentalで採用する。

- transportはApp Serverのstdio JSON-RPCを扱う。
- Codex固有のinitialize、thread/turn lifecycle、stream event、cancel、process lifecycleを専用adapter内へ閉じ込める。
- generic ACP clientを経由しない。Codex App ServerをACP serverとして設定しない。
- product use-caseはCodex protocolを知らず、reply、info、minutesの専用runtime protocolだけに依存する。
- capabilityは `reply`、会議中の `info`、会議後に利用者が明示実行する `minutes`、`stream`、`cancel` に限定する。
- coding tool、approval、filesystem操作、workspace write、任意command実行をMeeting Supporterの機能として公開しない。`security_boundary_verified=false` は維持する。
- PATH検出、version、login probeが`ready`を返した場合だけ選択・生成可能にする。`unknown`、`setup_required`、`unavailable`、`error`をreadyとして扱わず、安全な`action`だけを示す。
- experimental表示と制約を常に示し、一般提供を主張しない。

#### Codex情報AIのcomplete-note境界

Codexの `info` runtimeは、現在の会話メモと確定済み会話をtextとして受け取り、完全なMarkdownをtextとして返す。`context.md`、任意path読取、filesystem tool、workspace write、MCPは使わない。

- App Server turnはreply/minutesと同じ `approvalPolicy=never`、`sandbox=read-only`、tool/Web/MCP無効境界で開始する。
- hostは出力を20,000文字までに制限し、`# 会話メモ`、`## 決まったこと`、`## 未確認・懸念`、`## 次にすること` が各1回・この順で存在し、余分なH1/H2、コードfence、NUL、前後の説明がない場合だけ受理する。
- hostはturn開始時のmeeting IDと現在メモをsnapshotし、完了時に両方が一致する場合だけshared lock内でCAS更新する。invalid、empty、oversize、cancel、runtime error、meeting切替、同時更新conflictはcommitしない。
- Pydantic AIの `info` runtimeは既存のhost-owned `str_replace` toolによる部分更新を維持し、Codexのcomplete-note経路とfallbackまたはtool契約を共有しない。

#### Codex ephemeral turn subscription lifecycle

通常のCodex要求は、1要求につき1つの`ephemeral` threadと1つのturnを作成し、threadを継続・再利用しない。`thread/start`によって生じる通知購読は、そのthread IDを所有する`CodexTurn`が管理する。reply、info、minutesはuse-case別runtime adapterから同じread-only/tool-disabled turn開始境界を使う。

- `turn/completed`またはinterrupt要求の完了を処理してから、対象threadへ`thread/unsubscribe`を最大1回送る。
- user cancel、stream consumer cancellation、turn開始後のmodel rerouteまたはprotocol errorも同じ所有者がcleanupする。別threadや後続requestのlifecycleへ介入しない。
- processが既に終了または不確実な場合、cleanupのためにprocessを再起動しない。unsubscribe失敗は正常な生成結果を破棄せず、安全な診断だけを残す。
- unsubscribeは購読解除であり、ephemeral threadの即時削除を仮定しない。

#### Codexの互換性境界

Codex App Serverにはclient/server間のprotocol version negotiationがなく、生成schemaはCLI version固有である。一方、`experimentalApi=false`では公式にstable API surfaceへ限定できる。このためexact version allowlistをavailability境界にしない。

- `codex-cli 0.144.0`を最低安定版とし、これ未満、pre-release、不正なversion bannerは拒否する。
- schema fixtureを確認済みのversionは既知版として扱う。最低版以降の未検証安定版はhard blockせず、警告付きでruntime probeへ進める。
- 選択可能にする前に`initialize`、`account/read`、`model/list`の消費fieldとliteralをtyped modelで検証する。追加fieldは無視するが、必要fieldの欠落・型変更はfail closedとする。
- `thread/start`と`turn/start`は課金・外部処理を起こし得るため起動probeでは送らず、最初の利用時にtyped検証する。request受理後に不正responseが返る可能性もあるため、非互換なら応答を返さず不確実なprocessを停止する。
- active thread/turn宛ての既知notificationがschemaに違反した場合は黙って無視しない。streamをsafe errorで終了し、不確実なprocessの停止が完了するまでturn lockを保持する。未知methodと別thread/turn宛ての通知はforward compatibilityのため無視する。
- 複数binaryがある場合はschema確認済みの既知版を優先し、既知版がなければ最低版以降の安定版を選ぶ。非対応binaryを診断用fallbackとして残してもreadyにはしない。
- 新版の追従は毎回アプリreleaseを要求しない。fixture追加、schema差分、live smokeは互換性の観測と最低版更新判断に使い、通常のpatch/minor releaseを解禁するallowlistには使わない。

この方針は将来の後方互換性を仮定しない。各接続と各返答開始で、実際に消費するprotocol契約を検証する。

### 3. Generic ACPは別のexperimental runtimeとして維持する

ACPは標準化されたagent client protocolを検証する別route/runtimeであり、Codexの代替transportではない。

- 接続設定、session/capability negotiation、lifecycleはACP adapterが所有する。
- ACPの成熟度と互換性はACPとして評価する。
- Codexのversion、login、App Server schemaとは独立して提供状態を判断する。
- 一方の成功を他方のreadinessへ流用しない。
- command設定とprobeが`ready`を返した場合だけ選択・生成可能にし、それ以外はsafe `action`を示す。
- 一般向けのAI Usage surfaceにはACP cardを置かない。ACPは上級者向け設定だけで構成・診断し、一般向けの「このPCのChatGPTログイン」Codex cardとは表示、契約、readinessを共有しない。

### 4. BYOKとlocalはAdvancedへ分離する

BYOK cloud inferenceとOllama等のlocal/OpenAI-compatible inferenceは、現存する選択肢として維持するが上級者向けとする。一般画面にはAPIキー、provider、model、base URLという語を出さない。Advancedではデータ送信先と費用負担をroute選択と一緒に示す。

loopback以外のendpointを「このPC内」と断定しない。

### 5. Hosted serviceは通常buildでfail closedにする

Meeting Supporterが運営するhosted serviceのserver実装・運用文書は、このOSSリポジトリに含めない。通常のOSS buildではhosted serviceを未設定とし、次の状態から昇格させない。

```text
availability = planned
readiness = not_offered
selectable = false
selected = false
```

public clientに残る認証境界とschema validationは外部応答を信頼せず、未設定または不正な状態をfail closedで扱う。local STT、利用者自身のAPI credential、Ollama、Codex、ACPはhosted accountなしで利用できる。

### 6. Error boundaryを安全な契約にする

runtime内部の例外、prompt、stdout/stderr、token、credential、protocol payloadをUIへ渡さない。境界で次へ正規化する。

```text
code: stable safe code
message: user-actionable safe text
retryable: boolean
```

診断ログにもsecretや会議本文を安易に記録しない。binary不在、未ログイン、protocol非互換、timeout、cancel、異常終了を区別する。

### 7. 当面は外部Codexを検出し、binaryを再配布しない

このcutoverではユーザー環境のグローバル/PATH上にある公式Codexを検出して利用する。リポジトリやアプリbundleへCodex binary/npm packageを同梱しない。

将来同梱する場合は、実装開始前に以下をすべて通す。

1. 対象versionのApache-2.0ライセンスを確認し、配布物へ必要な `LICENSE` と `NOTICE` を含める。
2. npm registry metadataの正規package/versionと `dist.integrity` を固定・照合する。
3. OS/architectureごとの配布targetと取得artifactの対応表を管理する。
4. artifact checksumに加え、利用可能なupstream署名またはprovenanceを検証する。
5. 採用versionからApp Server schemaを再生成し、生成物の差分をreviewする。
6. request/notification/responseと互換性fixtureを使ったprotocol compatibility testを通す。
7. upgrade、rollback、vulnerability response、ライセンス更新の所有者を決める。

いずれかを満たさない限り同梱しない。

### 8. ローカル backend capability tokenをクラウド認証と扱わない

Tauri launcherがdesktop backend instanceごとに渡すcapability tokenは、ローカルprocessへの呼出しを確認する境界であり、hosted serviceの利用者認証ではない。直接起動するPython backendと`python-server`はローカル開発・動作確認用のままとし、そのまま公開serviceとして運用しない。この判断は現行のローカル認証または`python-server`の挙動を変更しない。

## Rejected Alternatives

### Codexをgeneric ACP経由で扱う

protocolとlifecycleが異なり、認証・version・schema・errorの責務を曖昧にするため採用しない。

### hosted serviceを選択可能なcardとして常設する

通常のOSS buildではhosted serviceが未設定であるため採用しない。必要な説明は「現在は提供していません」という情報表示に限る。

### API provider/modelを一般画面の主選択にする

一般利用者へ内部構成の理解を要求し、APIキー不要という価値を弱めるため採用しない。詳細はAdvancedへ隔離する。

### Codex binaryを直ちにbundleする

配布license、integrity、target、署名/provenance、schema互換性の継続運用が未整備なため採用しない。

## Consequences

### Benefits

- APIキー不要経路を実際のChatGPT login readinessに基づいて検証できる。
- Codex固有protocolとACP標準化検証を独立して進化させられる。
- hosted service未設定をUIとAPIが同じ意味で表せる。
- provider/runtime/configの語彙が一般向けコピーへ漏れにくい。
- 将来のbinary同梱が暗黙のsupply-chain変更にならない。

### Costs and risks

- 外部Codexのinstall/version/PATH差異を扱う必要がある。
- experimental routeが増える分、状態・復旧・E2E検証が必要になる。
- Advancedに既存のBYOK/local構成を移すUI作業が生じる。
- 同梱へ進む場合はrelease engineeringとlicense maintenanceが継続コストになる。

## Status and Supersession Chain

- ADR-009: **Accepted**。use-case/runtime/provider/config境界の現行authority。
- ADR-010: **Accepted**。route strategy、availability、Codex/ACP分離、配布gateの現行authority。

## Verification Required Before Stability Promotion

- Codex未導入、未ログイン、互換version、非互換versionの各readiness
- reply開始、stream、cancel、正常終了、timeout、process異常終了
- raw exception/prompt/stderr/token/credentialがAPI・WebSocket・UIへ出ないこと
- 通常のOSS buildでhosted serviceが`planned/not_offered/selectable=false/selected=false`であること
- CodexとACPが独立したruntime/readinessとして扱われること
- 対応OSの実デスクトップshellでのE2E

## Related Documents

- [ADR-009](./009-live-reply-llm-usecase-runtime-provider-architecture.md)
- [Product Requirements](../product/prd.md)
- [Product Surfaces](../ui/product-surfaces.md)
- [GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)
