# ADR-015: frontend-owned localizationとlocale-neutral UI message contractを採用する

- **Status**: Accepted
- **Date**: 2026-07-18
- **Builds on**: ADR-009、ADR-011、ADR-013
- **Supersedes**: API / WebSocketが利用者向け日本語文言を直接返す既存contract

## Context

Meeting Supporterのdesktop UIは日本語固定であり、ReactのcopyだけでなくPython API / WebSocketも利用者向けstatus、error、route説明を日本語stringとして返している。この境界のまま言語を追加すると、backendと複数windowがlocale、catalog、fallbackを重複して所有し、同じ状態が切替後も旧言語で残る。

会議音声の認識言語とUI表示言語は別の利用者意図である。transcript、reference、meeting title、AI reply/minutesなどのcontentもUI chromeの翻訳対象ではない。protocolはこれらを混同せず、raw provider error、exception、credential、path、prompt、会議contentを表示用payloadへ流さない必要がある。

## Decision

### 1. Localeとcatalogのauthority

frontendをUI locale、翻訳catalog、fallbackの単一authorityとする。backendはlocaleを受け取らず、解釈せず、翻訳しない。`Accept-Language` negotiationとserver-side catalogは導入しない。

embedded resourceは日本語と英語を提供し、英語をfallbackとする。初回は`navigator.languages`の先頭localeが`ja`または`ja-*`なら日本語、それ以外（unsupportedまたは空を含む）は英語とする。利用者の明示選択はdesktopに保持し、全WebViewへ同期する。UI localeとSTT localeは独立させる。

### 2. Protocol descriptor

APIとWebSocketの利用者向けstatus、error、route label / descriptionはhuman-readable stringではなく次のlocale-neutral descriptorを返す。

```text
UiMessage {
  code: stable lower-case domain/condition identifier
  values: string | integer | finite number scalar map
}
```

`code`は翻訳catalogの公開keyではなくprotocol identifierである。frontendはexhaustive mappingでcatalog keyへ変換し、render時に翻訳する。`values`はcount、seconds、HTTP status、provider proper nameなど表示を許可したscalarだけに限定する。exception、path、credential、prompt、transcript、provider response、raw subprocess outputは含めない。

未知の`code`、不足したvalue、不正なdescriptor、空のtranslationはprotocol errorとしてcodeやobjectを表示せず、frontendのgeneric error copyへ落とす。

### 3. Clean cutover

WebSocketのdisplay messageは`StatusMsg.text`、`ErrorMsg.text`、`SuggestionErrorMsg.text`を`message: UiMessage`へ置換する。HTTP/OpenAPIの表示対象fieldもstringから`UiMessage`へ置換する。旧日本語`text` / `message` field、alias、二重schema、互換shimは残さない。全producer、generated client、consumerを同じcandidateで移行する。

transcript、STT interim/final、AI note、reply chunk、minutesなど利用者またはAI contentの`text` fieldは変更しない。frontend stateはdescriptorを保持してrender時に翻訳し、翻訳済みstringやlocaleをbehavior判定に使わない。

### 4. Surface and formatting boundary

全visible copy、tooltip、dialog、empty/loading/error state、ARIA、日時・数値・通貨、native window titleをfrontend resourceとresolved localeから描画する。OS device名、file名、利用者入力、meeting title、transcript、reference、AI reply/minutes、license本文は原文のまま維持する。

日時・数値・通貨はresolved localeの`Intl` formatterを使う。言語切替は表示済みdescriptorとformat済み値を即時に再描画するが、STT設定またはcontentを変更しない。

## Rejected Alternatives

### Backendで翻訳する

APIとWebSocketへlocale negotiation、catalog、fallbackを重複させ、複数windowの即時切替と既存stateの再翻訳を難しくするため採用しない。

### `Accept-Language`でrequestごとに言語を選ぶ

WebSocketと保持済みasync stateを一貫して切り替えられず、desktop内のWebView間同期も解決しないため採用しない。

### 既存日本語string fieldを互換維持する

二つのauthorityが残り、callerがlocale-neutral descriptorへ移行したことをschemaで保証できないため採用しない。外部callerが判明した場合はschemaを二重化せず、issueをblockedへ戻してauthorityとacceptanceを改訂する。

### Raw errorをdescriptor valueへ入れる

translation interpolationを経由してexception、provider response、credential、path、prompt、会議contentが画面へ流出し得るため採用しない。

### UI localeとSTT localeを共用する

表示言語の変更が認識結果を変え、二つの独立した利用者意図を一つのpreferenceへ畳み込むため採用しない。

## Consequences

### Benefits

- 翻訳resourceの追加だけで新しいUI localeを増やせる。
- backend contractはlocale-neutralで、同じdescriptorを全windowが現在localeで描画できる。
- 言語切替後も保持済みstatus/error/date/count/ARIAを再翻訳できる。
- raw exceptionと利用者contentを表示用messageからschema上分離できる。

### Costs and risks

- WebSocket、OpenAPI、generated client、frontend stateを一度にclean cutoverする必要がある。
- protocol codeとcatalog mappingの完全性をPython、TypeScript、desktop scenarioで継続検証する必要がある。
- localStorageが利用できない環境ではsession内だけ変更を維持し、利用者へ保存失敗を通知する必要がある。
- 複数WebView間同期とnative titleの実desktop検証が必要になる。

## Localization verification

Verify the localization contract across the affected boundaries:

1. Move backend producers, the OpenAPI schema, generated client, frontend boundaries, state, and surfaces without compatibility fields.
2. Verify descriptors, catalogs, formatting, and native titles in frontend, Python, and Rust coverage.
3. In a real desktop shell, observe first locale resolution, immediate switching in both windows, restart persistence, system-language following, layout, and accessibility.

公開可能な実装進捗は[GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)で管理する。
## Supersession boundary

- ADR-009: **Accepted**。use-case / runtime / provider / config境界は維持する。
- ADR-011: **Accepted**。一般route surface責務を維持し、表示文言だけをdescriptorへ移行する。
- ADR-013: **Accepted**。credential control境界を維持する。
- 通常のOSS buildでhosted serviceが未設定の場合も、`not_offered`をlocale-neutral descriptorで表し、選択操作をfail closedで無効化する。
- ADR-015: **Accepted**。UI locale、catalog、fallbackとlocale-neutral display message protocolの現行authority。

## Related Documents

- [Product Requirements](../product/prd.md)
- [Product Surfaces](../ui/product-surfaces.md)
- [ADR Index](./README.md)
