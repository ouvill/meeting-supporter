# ADR-011: 一般設定にAI route cardを表示し構成値をAdvancedへ限定する

- **Status**: Accepted
- **Date**: 2026-07-15
- **Builds on**: ADR-009、ADR-010
- **Partially supersedes**: ADR-010 §3、§4の表示場所

## Context

ADR-010はCodex directとgeneric ACPを別runtime/readinessとして扱い、通常buildのhosted serviceを未設定とし、credentialやprovider/model語彙を一般画面から分離した。一方、一般設定からroute自体を隠すと、利用者は返答案がどこで処理され、誰が費用を負担し、現在利用できるかを判断できない。

routeの名称と利用条件は利用者の選択対象であり、credential・model・endpoint・command・runtime診断は構成詳細である。この二つを同じ表示境界に置かない。

## Decision

### 1. 一般設定にroute cardを表示する

一般設定では、現存するOpenAI、Gemini、Anthropic、Ollama、Codex、ACPの名称とroute cardを表示してよい。各cardはAPIのroute read modelを正本として、少なくとも次を示す。

- availability、readiness、selectable、selected
- 返答案capabilityの利用可否と安全なmessage/action
- 処理場所
- 費用負担
- experimental等のservice tier

一般設定はrouteを「返答案をどの支援方法で作るか」という利用者選択として表し、provider/runtime構造を理解させる画面にしない。Meeting Supporterが運営するhosted serviceのserver実装・運用文書はこのOSSリポジトリに含まれず、通常buildでは`not_offered/selectable=false`として選択可能に見せない。local STT、利用者自身のAPI credential、Ollama、Codex、ACPはhosted accountなしで利用できる。

### 2. 構成値と診断はAdvancedだけに置く

次はAdvancedでのみ表示・編集する。

- API keyその他のcredential
- provider固有のmodel識別子
- endpoint、base URL
- command、引数
- capability negotiation、runtime probe、version等の診断

一般設定のroute cardからAdvancedへ復旧導線を出してよいが、secretや内部診断をcardへ複製しない。

### 3. Codex directとgeneric ACPを統合しない

Codex directとgeneric ACPは別route、別runtime、別readinessを維持する。一方の検出、認証、成功を他方へ流用しない。一般設定に両方の名称/cardを表示できることは、protocolやlifecycleを統合する判断ではない。

### 4. 処理場所と費用負担を推測しない

UIはAPIの`data_location`と`billing_owner`を表示し、未知または不正な値を「確認できません」とする。Ollama等のendpointはloopbackだけを「このPC」とし、non-loopback endpointをlocalと断定しない。

## Rejected Alternatives

### routeをすべてAdvancedへ隠す

利用者が処理場所、費用負担、利用条件を比較できず、返答案が利用できない理由も不透明になるため採用しない。

### credentialやmodelまで一般cardへ表示する

一般利用者へ内部構成を要求し、secret露出と誤設定の範囲を広げるため採用しない。

### CodexとACPを同じローカルagent cardにまとめる

protocol、認証、readiness、障害復旧が異なり、状態を正直に表示できないため採用しない。

## Consequences

### Benefits

- 利用者は一般設定で支援方法、readiness、処理場所、費用負担を比較できる。
- 構成値と診断はAdvancedに限定され、通常導線は簡潔なままになる。
- Codex、ACP、BYOK、Ollamaの提供状態を隠さず、hosted service未設定も誤認させない。

### Costs and risks

- route metadataの欠落・不正値を安全に扱うUIとAPI testが必要になる。
- 一般向けcopyとAdvancedの技術語彙の境界を継続してreviewする必要がある。
- route追加時は処理場所、費用負担、capabilityを同時に定義する必要がある。

## Supersession

ADR-010のCodex direct、generic ACP分離、hosted service未設定、distribution gate、safe error boundaryは維持する。ADR-010 §3の「一般向けAI Usage surfaceにはACP cardを置かない」と§4のroute自体をAdvancedへ分離する表示判断だけを本ADRで置き換える。

## Related Documents

- [ADR-009](./009-live-reply-llm-usecase-runtime-provider-architecture.md)
- [ADR-010](./010-ai-route-strategy.md)
- [Product Requirements](../product/prd.md)
- [Product Surfaces](../ui/product-surfaces.md)
- [GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)
