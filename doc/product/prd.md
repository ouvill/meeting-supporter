# Product Requirements

- **Status**: Active
- **Updated**: 2026-07-16
- **Authority**: 何を提供するか、および現在の availability
- **Vision**: [Product Vision](./vision.md)

## Scope of This Document

本書はユーザー成果、機能要件、提供状態を定義する。実装方式の不可逆な判断は ADR、画面状態と文言は [Product Surfaces](../ui/product-surfaces.md)、現在の挙動はcode/tests、公開可能な進捗はGitHub Issuesを正本とする。

## Core User Journey

1. 利用者は音声入力と会議の目的・役割を確認する。
2. 必要なら参照資料を加え、会議を開始する。
3. アプリは会話を文字として示し、利用者が必要なときに返答案を要求できる。
4. 返答案は短く読みやすく表示され、利用者はコピー、言い換え、破棄を選べる。
5. AI経路が利用できない場合も、その理由と次の行動を安全な文言で確認できる。
6. 会議終了後、会話・返答案・録音など保存された成果物を振り返れる。

## Functional Requirements

### Meeting preparation

- 音声認識の準備状態と入力デバイスを確認できる。
- 会議の場面、利用者の役割、相手の役割、目的、制約を任意で指定できる。
- 対応する形式の参照資料を会議文脈へ追加できる。
- 開始できない場合は、理由と復旧操作を示す。

### Live assistance

- 相手と自分の発言、確定前後、聞き取り状態を区別して表示する。
- 手動の「返答案を作る」を主導線とする。
- main/ライブ支援ウィンドウはOS標準のwindow barを使い、その直下のapp toolbarから「常に前面に表示」の実状態を確認してON/OFFを切り替えられる。ライブ支援の選択は再起動後も復元され、mainとは独立する。
- 生成中の部分結果を表示し、利用者が停止できる。
- 停止・言い換え・破棄は対象の生成だけへ適用し、遅延した部分結果や完了通知で停止後の返答案を復活させない。
- 生成された返答案をコピーできる。
- 返答の意図を「標準」「丁寧」「短く」「確認する」「間をつなぐ」など利用者の言葉で選べる。
- 自動生成は初期状態で無効とし、明示的に有効化した場合だけ動作する。
- 生成失敗が文字起こし、会議終了、履歴保存を妨げない。
- ライブ支援のnative closeは会議中の状態を保ってwindowを隠し、mainのnative closeはアプリを終了する。
- 音声認識が準備済みなら、返答案が利用できない場合も会話の記録を開始できる。

### Post-meeting review

- 保存された会議を一覧・詳細で確認できる。
- 会話、返答案、利用可能な録音を確認できる。
- 会議タイトルを変更し、不要な会議を確認付きで削除できる。
- 会議後の要約はライブ返答の補助成果物として扱う。

### Setup and recovery

- 一般設定では「どの方法でAIを使うか」「準備できているか」「データをどこで処理するか」「誰が費用を負担するか」を理解できる。
- 一般設定ではOpenAI、Gemini、Anthropic、Ollama、Codex、ACPの経路名とroute cardを表示してよい。Gemini、OpenAI、AnthropicのAPIキーは対応する支援方法card、Deepgram、OpenAI、xAIのAPIキーは選択中の音声provider直下にあるprovider固有controlで入力・確認する。
- provider固有controlは保存済みAPIキーの値を再表示しない。model識別子、endpoint、command、runtime診断は上級者向け設定に置く。
- 接続テストまたは状態確認が失敗した場合は、同じprovider固有controlに秘密情報を含まない復旧案を示す。
- 未保存の設定変更がある状態で閉じるときは、`変更を破棄して閉じる`または`設定に戻る`を求める。破棄はform、secret draft/deletion、route選択をsaved baselineへ戻してから一度だけ閉じる。変更がなければ確認を挟まない。

### Hosted service boundary

Meeting Supporterが運営するhosted serviceのserver実装・運用文書は、このOSSリポジトリに含まれない。通常のOSS buildではhosted serviceは未設定で利用できず、`not_offered`かつ`selectable = false`としてfail closedする。local STT、利用者自身のAPI credential、Ollama、Codex、ACPはhosted accountなしで利用できる。

## AI Route Availability

### Status vocabulary

- `available`: サポート対象として利用できる。
- `experimental`: 試験提供。明示ラベル、既知の制約、失敗時の復旧手段が必要。
- `planned`: 方針上の候補だが、現在は利用できない。

readiness は availability と分ける。`ready`、`setup_required`、`unavailable`、`unknown`、`error`、`not_offered` など、現在の利用準備状態を表す。`selectable` はroute policyとして別に返し、UIで推測しない。Codex/ACPは`ready`のときだけ選択可能、BYOK/localは利用箇所で設定へ進むため選択可能とする。hosted serviceが未設定の通常buildは`not_offered/selectable=false`とする。生成実行はrouteにかかわらず`ready`と必要capabilityを要求する。

### Current product truth

| 利用者向け経路          | 対象                                           | availability | readiness                                                 |    selectable | データ                  | 費用負担         | 備考                                                                                                                                                                                                                   |
| ----------------------- | ---------------------------------------------- | ------------ | --------------------------------------------------------- | ------------: | ----------------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| このPCのChatGPTログイン | Codex App Serverを直接使う外部subscription経路 | `experimental` | 検出前は`unknown`、probe結果による                      | `ready`時のみ | 外部                    | 利用者の外部契約 | APIキー不要。手動replyと会議後に利用者が明示実行するminutesの生成・stream・cancelを対象とする。E2E品質は未確定で一般提供を主張しない。                                                                                 |
| 外部エージェント連携    | generic ACP runtime                            | `experimental` | command未設定は`setup_required`                         | `ready`時のみ | 構成による              | 外部契約         | 一般設定に経路名、card、readinessを表示してよい。commandと診断は上級者向け設定で構成する。Codex専用経路とは別runtimeであり、Codex App ServerをACPとして扱わない。                                                      |
| 自分のAIサービス        | BYOK cloud inference                           | `available`  | credential未設定は`setup_required`                        |      **true** | 外部cloud               | 利用者           | 一般設定にOpenAI、Gemini、Anthropicなどのprovider名とroute cardを表示してよい。APIキーは対応するroute cardで入力・確認し、保存済み値、model識別子、endpointは表示しない。model識別子とendpointの編集はAdvancedに置く。 |
| このPCで処理            | Ollama等のlocal inference                      | `available`  | service停止は`unavailable`、model未導入は`setup_required` |      **true** | localまたは指定endpoint | 利用者           | 一般設定にOllamaなどのservice名とroute cardを表示してよい。endpointとmodelの編集はAdvancedに置き、loopback以外はlocalと断定しない。                                                                                    |
| Hosted service          | 通常のOSS buildでは未設定                      | `planned`    | `not_offered`                                             |     **false** | 未設定                  | 未設定           | server実装と運用文書はこのリポジトリに含まれない。                                                                                                                                                                    |

### Route read model

利用経路の読み取りモデルは次のフィールドを一つの単位として返す。

```text
id
kind
label
description
availability
readiness
selectable
selected
data_location
billing_owner
capabilities
reason_code
message
action
```

要件:

- `id` は安定した機械識別子、`label` と `description` は利用者向け表現とする。
- `availability`、`readiness`、`selectable` を別々に返し、UIで推測しない。
- `selected = true` は `selectable = true` の経路に限る。
- `data_location` と `billing_owner` を選択前に理解できる。
- `capabilities` は実際に提供するユースケースだけを列挙する。Codexは `reply`、`minutes`、`stream`、`cancel` に限定する。
- `reason_code`、`message`、`action` は安全な値とする。raw exception、prompt、stderr、token、credentialを返さない。

## Architecture-neutral Boundaries

UIと要件で次の語を混ぜない。

- **Route**: 利用者が選ぶ「AIを使う方法」。availability、データ、費用の単位。
- **Runtime**: 実行プロトコルとprocess lifecycleを吸収する内部実装。
- **Provider/model**: 推論先とモデル解決。provider名は利用者向けroute情報として表示でき、provider固有のAPI credential controlは対応するSettings利用箇所に置ける。保存済みcredential、model識別子、endpoint等は一般surfaceへ表示しない。
- **Config/secret**: 永続設定とcredential保存。routeやruntimeそのものではない。

use-caseはruntime種別を知らず、runtimeはroute表示文言を決めない。ADR-009の use-case / runtime / provider / config 境界を維持する。

## Cross-cutting Requirements

### Trust and privacy

- 外部送信前にデータロケーションを表示する。
- credential、会議本文、生成prompt、token、raw subprocess出力を診断表示やroute responseへ含めない。
- 利用者の操作なしに外部へ発話しない。
- local表示は実際の接続先に基づき、remote endpointをlocalと誤表示しない。
- desktop backendのlocal capability tokenは、その端末のTauri launcherが起動したprocessへの呼出しを確認するだけで、hosted serviceの利用者認証ではない。直接起動するPython backendと`python-server`はローカル開発・動作確認用であり、そのまま公開serviceとして運用しない。

### Reliability

- AI経路の失敗を文字起こし・会議制御から隔離する。
- streamingとcancelの終了状態を一意に扱う。
- process終了、timeout、認証切れは安全なcode/message/retryableへ正規化する。
- experimental経路には利用不能時の復旧行動を示す。

### Accessibility and language

- 主操作はキーボードでも到達でき、状態は色だけに依存しない。
- 会議中の文言は短く、内部技術用語を避ける。
- desktop UIは日本語と英語を提供する。初回起動時は`navigator.languages`の先頭localeが`ja`または`ja-*`なら日本語、それ以外（unsupportedまたは空を含む）は英語を選ぶ。利用者の明示選択を以後保持し、UI localeと会議音声の認識localeは独立させる。

## Non-goals

- 自動発話、相手への自動送信、無人会議bot
- 汎用coding agent UIや任意tool実行UI
- provider/runtimeの全機能を一般利用者へ露出すること
- 通常のOSS buildで未設定のhosted serviceを利用可能に見せること
- experimental経路を安定提供として表示すること


## Release Gates

経路を一般提供へ昇格するには、少なくとも以下が必要である。

- 対応OSで準備・生成・stream・cancel・終了をE2E検証した証拠
- 認証切れ、binary不在、timeout、異常終了の安全な復旧
- データロケーション、費用負担、既知の制約の表示
- 配布物と依存物のlicense/security review
- UIがavailability/readiness/selectableを忠実に表す検証
- 昇格判断を記録するAccepted ADR

## Change History

| Date       | Change                                                                                           |
| ---------- | ------------------------------------------------------------------------------------------------ |
| 2026-07-20 | OSS公開境界を明確化し、hosted serviceの内部要件をprivate authorityへ分離。                       |
| 2026-07-16 | provider固有API credential controlをSupport/Audioの利用箇所へ移し、設定破棄copyを確定。          |
| 2026-07-10 | 旧product文書から公開可能な恒久要件を統合し、route availabilityを一意化。                        |
