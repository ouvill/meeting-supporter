# ADR-009: Live Reply 中心の LLM use-case / runtime / provider 境界を採用する

- **Status**: Accepted
- **Date**: 2026-07-08

## Context

会議支援AIの主価値は、汎用議事録や汎用チャットではなく、会議中に相手の発言へすぐ使える「次の一言」を提案する Live Reply にある。

一方で、既存ドキュメントと実装には以下の混線が残っている。

- `reply_agents`, `info_agent`, `minutes_agent` が同じ「agent」語で扱われ、user-visible な返答候補と hidden updater / post-meeting generator の境界が曖昧。
- LLM provider、model assignment、runtime adapter、use-case wiring が `factory.py` 周辺に集まりやすい。
- ADR-006 / ADR-008 は runtime 境界の方向性を示しているが、最終的な product use-case / runtime / provider / config の層構造までは決めていない。
- 当時のtemporary provider-layer designは新形式優先・移行戦略なしを前提にし、clean cutover方針と矛盾していた。

## Decision

以下の4層を目標アーキテクチャとして採用する。

```text
Product Use-case Layer
  -> Runtime Layer
    -> Provider / Model Layer
      -> Config / Secret Layer
```

### 1. Product use-case layer

Product behavior の単位で分ける。

- `ReplyPipeline`
  - Live Reply の主経路。
  - 会話文脈、meeting context、reference context、suggestion mode から「次の一言」を作る。
- `InfoNoteUpdater`
  - 会議中の hidden updater。
  - AI note / context 補強 / 検索用メモ更新を扱う。
  - user-visible な返答候補として扱わない。
- `MinutesGenerator`
  - 会議後の議事録・要約生成。
  - Live Reply の同期 UX とは分ける。
- `MeetingContextService`
  - 会議ごとの role / objective / constraints / reference documents を管理する。
- `UsageLogger`
  - agent/model/token/cost の記録を行う。

Use-case layer は runtime 種別を直接知らない。Pydantic AI、ACP、OpenAI-compatible、Ollama などの差分は下位層に閉じ込める。

### 2. Runtime layer

LLM / 外部 agent 実行方式の違いを吸収する。

- `PydanticAIReplyAgentRuntime`
- `PydanticAIInfoAgentRuntime`
- `PydanticAIMinutesAgentRuntime`
- `ACPReplyAgentRuntime`
- future: `CodexRuntime`, `ClaudeSdkRuntime`, local runtime など

Runtime が扱うもの:

- streaming
- process 起動 / 終了
- external protocol adapter
- tool / toolset 接続
- usage logging hook
- permission boundary
- provider-specific SDK object construction

Runtime は use-case 別 Protocol を満たす。現時点で汎用 `AgentRuntime` へ統合しない。

### 3. Provider / model layer

Provider / model / secret / data location を管理する。

- `ProviderDefinition`
- `ProviderRegistry`
- `ModelResolver`
- `RuntimeFactory`
- `SecretStore`
- `LlmAssignmentConfig`

採用する model 参照形式:

```text
provider_id/model_name
```

legacy `kind:model` は互換入力として受け付け、内部で `provider_id/model_name` へ正規化する。

Provider kind の扱い:

- `google-gla`, `openai`, `anthropic`, `ollama`
  - Pydantic AI の built-in model inference 経路へ写像する。
- `openai-compatible`
  - `OpenAIChatModel` を明示構築する。
- `acp`
  - experimental な reply runtime provider として扱う。
  - Pydantic AI model provider としては扱わない。
- `codex`, `claude-sdk`
  - future / spike 候補。production path にはしない。

### 4. Config / secret layer

外部 schema と内部表現を分ける。

未リリースのため、外部 config / Settings API / generated frontend types も必要なら破壊的変更してよい。ただし、破壊は一括で雑に行わず、fixture・変換方針・検証を伴う clean cutover とする。

現在読み取り互換がある legacy schema:

```toml
[agents]
reply_enabled = true
reply_auto_generate = false
reply_main = true
reply_polite = true
info_enabled = true

[[reply_agents]]
id = "reply_main"
label = "標準"
enabled = true
priority = 10
custom_instruction = ""
```

現在の assignment schema:

```toml
[llm_assignments]
reply_model = "gemini/gemini-3.1-flash-lite"
info_model = "ollama/qwen3"
minutes_model = "ollama/qwen3"
```

目標外部 schema:

```toml
[reply]
enabled = true
auto_generate = false
default_style = "standard"

[[reply.styles]]
id = "standard"
label = "標準"
enabled = true
priority = 10
instruction = ""
```

`reply_agents` -> `reply.styles` rename は採用する。ただし、実施は dedicated migration phase で行い、Settings API / generated frontend type / config fixtures / load-save tests を同じ cutover に含める。

`sos` 専用 agent/runtime/config は増やさない。必要な SOS 的応答は `buy_time` / `clarify` / `push_back` などの suggestion mode として `ReplyPipeline` に吸収する。

## Non-decisions

- `reply_agents` を `reply.styles` に rename する具体的な cutover 手順はこの ADR では決めない。
- `[agents].reply_main` / `[agents].reply_polite` の削除タイミングは migration phase で決める。
- Settings API / generated frontend types の最終 shape は migration phase で決める。
- ACP / Codex / Claude SDK / subscription runtime を production-ready として採用しない。
- 汎用 `AgentRuntime` を導入しない。
- 汎用 agent 管理 UI を作らない。
- ユーザーを介さない自動応答ボット化を目標にしない。

## Consequences

### Pros

- Live Reply を product の中心に置いたまま、LLM 実行方式だけを差し替えられる。
- `reply`, `info`, `minutes` の責務境界が明確になる。
- provider/model 解決と runtime construction が use-case wiring から分離される。
- 未リリース前提のため、互換 shim を長期維持せず clean cutover できる。
- ACP や subscription runtime は experimental / spike として隔離できる。

### Tradeoffs

- 破壊的変更を許容するため、config / API / generated types / UI の同時更新漏れがリスクになる。
- `reply_agents` という名前は cutover まで product 概念として不正確なまま残る。
- runtime adapter が use-case 別に増えるため、薄い重複は発生する。
- migration phase では fixture と smoke test が必須になる。

## Migration policy

未リリースなので clean cutover を優先する。段階は小さく分けるが、不要な backward-compatible shim を長期維持しない。

1. 内部の provider/model 解決と runtime construction を分離する。
2. `factory.py` を bundle assembly / use-case wiring に縮小する。
3. internal model を `ReplyPipeline` / `InfoNoteUpdater` / `MinutesGenerator` へ寄せる。
4. UI 用語を「agent」から「返答スタイル / 提案モード」へ切り替える。
5. `reply_agents` -> `reply.styles`、legacy `[agents].reply_main` / `[agents].reply_polite` 削除、Settings API / generated frontend types 更新を dedicated migration phase で clean cutover する。
6. cutover 後に不要な compatibility shim を削除する。

公開可能な実装進捗は[GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)で管理する。

## Related Documents

- [ADR-010: AI route strategy](./010-ai-route-strategy.md)
- [Product Requirements](../product/prd.md)
- [Product Surfaces](../ui/product-surfaces.md)
- [GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)
