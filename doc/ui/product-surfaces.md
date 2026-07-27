# Product Surfaces

- **Status**: Active
- **Updated**: 2026-07-18
- **Authority**: 画面責務、状態、一般向けコピー
- **Requirements**: [Product Requirements](../product/prd.md)

## Experience Principles

1. 会議中は「聞く」「返答案を作る」「コピーする」以外の判断を増やさない。
2. 一般向け画面では内部用語を使わず、利用者の目的で説明する。
3. `available`、`experimental`、`planned` と、現在の `readiness` を混同しない。
4. 状態は色だけで伝えず、短いラベルと次の操作を添える。
5. unavailableな操作を押せるように見せない。
6. AI失敗時も会議の終了、履歴、設定への退避を残す。
7. OS標準のwindow操作とアプリ内navigation/statusを混ぜない。

## Language and Localization Contract

- desktop UIの全visible copy、tooltip、dialog、empty/loading/error state、ARIA label/description、日時・数値・通貨、native window titleを日本語と英語で提供する。
- 初回は`navigator.languages`の先頭localeが`ja`または`ja-*`なら日本語、それ以外（unsupportedまたは空を含む）は英語とする。利用者が表示設定で`システム設定 / System default`、`日本語`、`English`を選ぶと即時に全windowへ反映し、明示選択を次回起動へ保持する。
- `システム設定 / System default`は起動時とruntimeのOS language変更時に再解決する。fallback catalogは英語とし、未知または不完全な翻訳は安全なgeneric copyへ落とす。
- UI localeと音声認識のlocaleは別のpreferenceである。表示言語の変更はSTT設定または会議contentを変更しない。
- OS device名、file名、利用者が付けたmeeting title、transcript、reference、AI reply/minutes、利用者定義のreply style label、license本文は原文のまま表示する。
- APIとWebSocketの利用者向けstatus/error/route説明はhuman-readable stringではなく`UiMessage` descriptorを返す。backendはlocaleを解釈せず、frontendだけがdescriptorを翻訳する。raw exception、path、credential、prompt、transcript、provider responseをdescriptor valuesまたは画面へ出さない。
- localeや翻訳済み文言をbehavior分岐へ使わない。severity、message code、readinessなどのstable fieldで状態を判定する。

## Vocabulary and Copy Rules

### General surfaces

一般画面で使用する語:

- `AIの使い方`
- `このPCのChatGPTログイン`
- `OpenAI`、`Gemini`、`Anthropic`、`Ollama`、`ACP`などの経路名
- `自分で接続する`
- `データの処理場所`
- `費用は外部サービスの契約に基づきます`
- `現在は提供していません`

一般画面で表示しない情報:

- 保存済みAPIキー、credentialの既存値
- model識別子、base URL、endpoint、command
- adapter、JSON-RPC、stdio、ACP capability、runtime診断
- token、raw error、stderr、stack trace
- BYOK（単独の略語として）

providerやserviceの名称とroute cardは一般設定に表示してよい。例外として、Settingsの対応する利用箇所にprovider固有のAPIキー入力・接続確認controlを表示できる。保存済み値、model、endpoint、command、診断詳細は表示しない。

### Availability and readiness labels

availabilityとreadinessを一つのstatusへ畳み込まない。

| availability | General label | Rule                          |
| ------------ | ------------- | ----------------------------- |
| available    | ラベルなし    | readinessを併記する。         |
| experimental | 試験提供      | 常時badgeと既知の制約を示す。 |
| planned      | 提供前        | 選択操作を置かない。          |

| readiness      | General label          | Supporting copy                                | Primary action      |
| -------------- | ---------------------- | ---------------------------------------------- | ------------------- |
| ready          | 準備できました         | この方法で返答案を利用できます。               | この方法を使う      |
| setup_required | 準備が必要です         | 利用前に接続またはログインを確認してください。 | APIの`action`に従う |
| unknown        | 確認できていません     | 現在の利用状態をまだ確認できません。           | APIの`action`に従う |
| unavailable    | 現在利用できません     | APIのsafe `message`を表示する。                | APIの`action`に従う |
| not_offered    | 現在は提供していません | 提供時期・価格は未定です。                     | なし                |

`reason_code` は分岐にだけ使い、そのまま画面へ表示しない。`message` と `action` はAPIが返す安全な値だけを表示する。

## Surface Map

```text
起動・準備
  -> 会議中メイン
      -> ライブ支援パネル
  -> 会議履歴
設定
  -> AIの使い方
      -> 上級者向け設定
  -> 音声認識
  -> データと保存
```

## 1. Start / Preparation

### Purpose

会議を始められる状態かを短時間で確認し、会議文脈を任意で補う。

### Content

- アプリと音声認識の準備状態
- 相手の声、自分の声の入力選択
- 会議の場面、役割、目的、制約
- 参照資料の追加と受理/拒否結果
- `会議を始める`
- `AIの使い方` の要約状態
- 履歴、設定への導線
- OS native window bar直下のapp toolbarにbrand、home、履歴、設定、mainの前面固定を置く。会議中はbrand、`会議中`、設定、前面固定だけに絞る。

AI routeの詳細カード一覧は置かない。準備できていない場合だけ、`AIの準備を確認` を示す。

### States

- **booting**: `アプリを準備しています…`
- **audio_not_ready**: `音声認識の準備が必要です` / `音声認識を使えるようにする`。実行前に`初回は必要なデータの読み込みに時間がかかる場合があります。`と示す。
- **audio_preparing**: `音声認識を準備しています…`とprogressを一つ表示し、二重起動を防ぐ。完了時は`音声認識を使えます`、失敗時は`音声認識を準備できませんでした。もう一度お試しください。`と示す。
- **ready**: `会議を始められます`
- **partial**: 音声は使えるがAIが使えない場合、`会話の記録は開始できます。返答案は現在利用できません。`
- **error**: safe messageと`もう一度試す`。raw detailは表示しない。

## 2. Main Meeting Window

### Purpose

会議の進行、音声状態、経過時間、ライブ支援ウィンドウを制御し、必要なときだけ直近までの会話を確認できるようにする。

### Content

- `会議中` と経過時間
- 自分/相手の音声レベルと聞き取り状態
- 会議を終了する主操作
- `ライブ支援を表示`
- 最小限の設定導線
- OS native window bar直下のapp toolbar。会議中は効果のないnavigationを表示せず、設定とmainの前面固定だけを残す。
- 初期状態では閉じた`会話履歴`。確定発言と聞き取り中の発言を同じ時系列で確認する。

会話履歴は会議操作を圧迫しない高さに制限し、パネル内だけをスクロールさせる。AI方式の選択、モデル、費用詳細は置かない。

### States

- **listening**: `聞き取り中`
- **no_input**: `音声が届いていません` / `入力を確認`
- **assistant_unavailable**: `返答案は利用できません。会議の記録は続けられます。`
- **ending**: `会議を保存しています…`。終了操作を重複送信しない。
- **saved**: `会議を保存しました`

mainのnative closeはアプリを終了する。minimize、maximize、restore、closeはOSへ委ね、HTMLで再実装しない。

## 3. Live Assistance Panel

### Purpose

ビデオ会議の横で、最新の会話文脈だけを確認しながら、必要な瞬間に返答案を得る。

### Layout

1. OS native window bar直下のapp toolbar: `会議中`、聞き取り状態、前面固定
2. `常に前面に表示` の実状態とON/OFF操作。Main Windowとは独立し、利用者の選択を再起動後も復元する。
3. 最新の発言を1件だけ表示する短い文脈領域。全会話履歴はMain Meeting Windowで確認する。
4. 返答案領域
5. `返答案を作る` と、少数の意図選択

内部route、model、token、単価、設定フォームは置かない。

### Reply states

| State             | Display                                                            | Actions                  |
| ----------------- | ------------------------------------------------------------------ | ------------------------ |
| idle              | `必要なときに「返答案を作る」を押してください`                     | 返答案を作る             |
| no_context        | `相手の発言が聞き取れると返答案を作れます`                         | なし                     |
| generating        | `返答案を作っています…` とpartial text                             | 停止                     |
| ready             | `返答案` と1つの主結果                                             | コピー、言い換える、破棄 |
| cancelled         | `返答案の生成を停止しました`。partial textは確定結果と混同しない。 | もう一度                 |
| disabled          | `返答案はオフです`                                                 | 設定へ                   |
| route_unavailable | `AIの準備を確認してください` とsafe reason                         | 準備を確認               |
| error_retryable   | `返答案を作れませんでした`                                         | もう一度                 |
| error_terminal    | `この方法は現在利用できません`                                     | AIの使い方へ             |

### Interaction

- 手動生成を既定にする。
- 生成中も最新の発言を隠さない。長い会話ログをプロンプターへ持ち込まない。
- 新しい発言が入っても現在の返答案を勝手に置換しない。
- 1つの主結果を読みやすく示し、候補カードを無制限に増やさない。
- コピー成功は短時間の`コピーしました`で伝える。
- 支援ウィンドウ内で`常に前面に表示`をON/OFFでき、現在状態を色だけに依存せず確認できるようにする。
- `停止`はcancelを送り、完了後にgeneratingへ戻らない。
- stop/retry/言い換え/破棄は`generation_id`で対象を区別し、cancelled/discarded generationの遅延chunkや完了を表示・保存しない。
- shortcutは入力欄・ボタン操作と衝突させない。
- native closeはwindowを破棄せずhideし、再表示時に会議中の状態と前面固定状態を保つ。

## 4. AI Usage Setup

### Purpose

専門知識なしで、利用できるAIの方法と現在の準備状態を理解する。

### Route cards

カードはAPIのroute read modelだけから描画する。

表示順:

1. 選択中かつ利用可能な方法
2. 準備済みの方法
3. 準備が必要な方法（OpenAI、Gemini、Anthropic、Ollama、ACPなどの個別route cardを含む）
4. hosted service未設定の説明（選択カードではない）

各選択可能カードには `label`、短い`description`、安定/試験提供、readiness、データの処理場所、費用負担、safe `message` と `action` を表示する。

### Required route copy

#### Codex direct

- Label: `このPCのChatGPTログイン`
- Badge: `試験提供`
- Ready: `ChatGPTへのログインを確認しました`
- Missing binary: `Codex CLIがインストールされていないか、見つけられません` / `Codex CLIの入手方法を見る`
- Logged out: `ChatGPTへのログインが必要です` / `ログイン方法を見る`
- Constraint: `会議前に接続を確認してください。動作が不安定な場合があります。`

version番号やApp Serverという語は診断詳細/Advancedにのみ表示する。アプリ内ではCodex CLIのインストールを実行せず、公式の案内ページを開く。インストール・更新後はアプリの再起動を案内する。

#### Generic ACP

- 一般向けの`AIの使い方`にACPのroute card、試験提供badge、readinessを表示してよい。
- command、capability、接続診断は`上級者向け設定`でのみ表示・編集する。
- Advancedのcommandはshell文字列ではなくargvとして1行につき1引数で編集し、shell展開しない。
- `ready`はcommand設定済みを表すだけで、agentへの接続成功を装わない。編集中は保存済み設定のreadinessと区別する。
- Codex cardとreadinessや説明を共有せず、Codex App ServerをACPとして扱わない。

#### BYOK/local

一般設定にOpenAI、Gemini、Anthropic、Ollamaなどのprovider/service名、個別route card、readinessを表示してよい。Gemini、OpenAI、Anthropicのroute card内にはprovider固有のAPIキー入力・接続確認controlを置く。mappingにないBYOK routeへ認証方式を推測しない。Deepgram、OpenAI、xAIのcloud音声認識を選んだ場合は、Audioの選択欄直下へ同じcontrolを置く。同じOpenAI credentialは両surfaceでdraft、保存済み状態、検証message、削除予定を共有する。

保存済みAPIキーの値は表示しない。model識別子、endpointの表示・編集はAdvancedに残す。

#### Hosted service

Meeting Supporterが運営するhosted serviceのserver実装・運用文書は、このOSSリポジトリに含まれない。通常のOSS buildではhosted serviceは未設定で利用できず、`not_offered`かつ`selectable = false`として表示する。login、checkout、hosted route選択を開始できる操作は置かない。

local STT、利用者自身のAPI credential、Ollama、Codex、ACPはhosted accountなしで利用できる。public clientに残る認証境界とschema validationは外部応答を信頼せず、未設定または不正な状態をfail closedで扱う。


## 5. Advanced AI Settings

### Purpose

provider固有model、local service、外部agent runtimeを理解している利用者が、API credential以外の構成値と診断を管理する。

### Content

- cloud provider固有のmodel識別子
- Ollama/OpenAI-compatible endpointとmodel
- Vosk model path
- generic ACP commandと接続状態
- Codex診断情報（検出version、login readiness）
- data locationとbilling owner

### Rules

- API credentialの既存値はSupport/Audioを含む全surfaceで再表示しない。
- secretをcopyable text、URL query、error detailへ出さない。
- loopbackでないlocal-compatible endpointには`このPC外へ送信される可能性があります`を表示する。
- API credentialの接続確認と設定全体の保存を区別する。
- failed testで保存済みの値を勝手に消さない。

## Settings dialog behavior

- Settingsはnative `<dialog>`のtop layerへ表示し、titleへ初期focusを置く。
- Tab/Shift+Tabはdialog内を循環し、Escape、backdrop、close操作は同じclose requestを使う。
- 変更がなければ直ちに閉じ、変更があれば`変更を破棄して閉じる` / `設定に戻る`を表示する。確認dialogのcloseとEscapeはSettingsと変更を保持する。
- `変更を破棄して閉じる`はform、secret draft/deletion、未保存のroute選択をsaved baselineへ戻してから一度だけ閉じる。
- 実際に閉じた後、起点要素が存在する場合だけfocusを戻す。

## 6. Meeting History

### Purpose

ライブ支援の副産物を確認し、必要な記録を保持・削除する。

### Content

- 会議一覧、日時、長さ、保存成果物の有無
- 会議詳細、会話、保存された返答案、利用可能な録音
- タイトル変更
- 確認dialog付き削除

### States

- loading: skeletonまたは`読み込み中`
- empty: `保存された会議はありません`
- load_error: `履歴を読み込めませんでした` / `もう一度`
- deleting: dialog操作を無効化し`削除しています…`
- missing_asset: 会議全体をerrorにせず、該当成果物だけ`利用できません`

### Post-meeting minutes states

会議が`completed`で書き起こしがある場合だけ、振り返り詳細に`要約・議事録を作成`を表示する。表示だけで外部送信や生成を開始してはならず、利用者が明示操作したときだけ開始する。

| State             | Display                                                | Actions                   |
| ----------------- | ------------------------------------------------------ | ------------------------- |
| route_unavailable | `AIの準備を確認してから作成できます。`                 | disabled button、設定確認 |
| generating        | `要約・議事録を作成しています…` とpartial text         | `生成を中止`              |
| cancelled         | `生成を停止しました。途中の内容は保存されていません。` | もう一度作成              |
| error             | APIのsafe message                                      | もう一度作成              |
| completed         | 保存済みの要約・議事録全文                             | `要約・議事録を作り直す`  |

cancelは当該HTTP streamをabortするだけで、別use-caseのCodex turnへglobal cancelを送らない。完了全文だけを対象会議へ保存し、再表示する。

## Error and Privacy Copy Contract

UIへ表示してよいもの:

- safe codeに対応する短いmessage
- retryableかどうか
- 再確認、設定、ログイン手順へのaction
- data locationとbilling owner

表示してはならないもの:

- raw exception / stack trace
- prompt / transcript dump
- subprocess stdout/stderr
- access token / API credential / auth header
- local absolute path（利用者が明示選択したpath表示を除く）

不明なerrorを推測して`ログインが必要`等へ変換しない。安全なgeneric copy `処理を完了できませんでした` と recovery actionを使う。

## Review Gate

一般画面を変更するPRは、少なくとも次を確認する。

- route/provider/runtime/configの語が混ざっていない
- 通常のOSS buildでhosted serviceが`not_offered/selectable=false`になっている
- experimental badgeと制約が常時見える
- loading/empty/ready/disabled/error/cancelledを扱う
- keyboard、focus、色以外の状態表現がある
- unsafe detailがUIへ流れない
- PRDのavailabilityと一致する
- 重要surfaceをbase stateで表示し、opacity animation成功へ依存させない
- 対応OSのnative shellでwindow close/前面固定/返答案controlを実行し、WCAG A/AAの自動検査を通す

実装進捗と公開可能なbug・featureは[GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)で管理する。
