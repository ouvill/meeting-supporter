# ADR-013: API credential controlを利用箇所へ配置する

- **Status**: Accepted
- **Date**: 2026-07-16
- **Partially supersedes**: ADR-011 §2のAPI key配置
- **Builds on**: ADR-009、ADR-010、ADR-011

## Context

ADR-011は一般設定へroute cardを表示しつつ、API keyを含む構成値をAdvancedだけに置く境界を採用した。この境界では、利用者がGemini、OpenAI、Anthropicの支援方法を選んだ後、離れたAdvancedへ移動してproviderを探す必要がある。Deepgram、OpenAI、xAIのcloud音声認識でも、選択箇所とcredential入力が分離しているため、どのAPI keyが必要か、どこで復旧するかが分かりにくい。

一方、保存済みsecretの再表示、model、endpoint、command、runtime診断まで一般surfaceへ移すと、secret安全策とroute/runtime/config境界を曖昧にする。同じOpenAI credentialは支援方法と音声認識の両方で使われるため、surfaceごとに独立したdraftや検証stateを持つこともできない。

## Decision

### 1. provider固有のAPI credential controlを利用箇所へ置く

- Gemini、OpenAI、AnthropicのAPI credential controlは、支援方法の対応するroute card内へ置く。
- Deepgram、OpenAI、xAIのAPI credential controlは、音声設定で選択中のcloud provider直下へ置く。
- mappingにないBYOK routeへ認証方式を推測してcontrolを表示しない。
- API key未設定でも選択可能なBYOK routeは、従来どおり選択できる。保存時に不足を検出した場合は、対応するSupportまたはAudioのcontrolへ戻す。

### 2. secretの安全境界と送信境界を維持する

保存済みsecretの値は取得・再表示・placeholder化しない。保存済み状態では既存keyを使う接続確認、空欄からの変更、削除予定の指定だけを提供する。入力値を送信する境界はprovider固有の接続確認と設定全体の保存だけとし、全体保存時はOS credential storeへ移す既存契約を維持する。

### 3. OpenAI credential stateを両surfaceで共有する

支援方法と音声認識に現れるOpenAI controlは、同じsecret draft、保存済み状態、検証状態、検証message、削除予定を参照する。一方で入力したdraftは両方へ即時反映し、個別surface用の複製stateを作らない。

### 4. Advancedに残す構成値を限定する

provider固有のmodel、Ollama/OpenAI-compatible endpoint、Vosk model path、ACP command、runtime診断はAdvancedに残す。API credentialの全provider一覧はAdvancedから削除する。一般surfaceへ表示できる構成値は、上記provider固有のSettings controlにある未保存API key入力と接続状態だけであり、保存済み値、model、endpoint、command、runtime診断は表示しない。

## Rejected Alternatives

### API credentialをAdvancedだけに置く

利用箇所と復旧箇所が離れ、選択したproviderと必要なcredentialの対応を利用者へ再判断させるため採用しない。

### route cardまたはAudioから共通dialogを開く

入力と状態が利用箇所から隠れ、複数providerを比較・復旧するときに文脈を失うため採用しない。

### surfaceごとに独立したsecret stateを持つ

同じOpenAI credentialに異なるdraft、検証結果、削除予定が存在し得るため採用しない。

## Consequences

### Benefits

- providerの選択、API key入力、接続確認、復旧が同じsurfaceで完結する。
- 未設定credentialの保存errorを実際の入力箇所へ戻せる。
- OpenAIを支援方法と音声認識で使っても、secretと検証stateが一意になる。
- 保存済みsecret非表示とOS credential store境界を維持できる。

### Costs and risks

- 同じprovider controlが複数surfaceへ現れるため、draft、検証、削除予定の共有stateを一元管理する必要がある。
- route IDとcredential providerのmappingを明示的に保守する必要がある。
- 狭いSettings viewportでもroute card内のinputとactionがfooterに隠れないことを実shellで検証する必要がある。

## Supersession

本ADRはADR-011 §2のうち「API keyはAdvancedだけに置く」という配置判断だけを部分的にsupersedeする。ADR-011のroute visibility、route metadata、readiness、selectable、Codex directとgeneric ACPの分離、model・endpoint・command・runtime診断をAdvancedへ置く境界は維持する。ADR-009のuse-case / runtime / provider-model / config-secret境界と、ADR-010のroute strategyも変更しない。

## Related Documents

- [ADR-011](./011-general-route-card-visibility.md)
- [Product Requirements](../product/prd.md)
- [Product Surfaces](../ui/product-surfaces.md)
- [GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)
