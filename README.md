# Meeting Supporter

会議中の会話を聞き取り、利用者が必要なときに短い「次の一言」を提案するデスクトップ支援アプリです。AIが自動で相手へ発話・送信するのではなく、利用者が内容を確認して自分で使います。

> このリポジトリは未リリースです。以下は現在リポジトリに実装されている挙動と、明示的に分離したexperimental/planned項目だけを記載します。

## 現在できること

### 会議の準備

- マイクとシステム音声の入力デバイスを選択する
- 音声認識を準備して状態を確認する
- 会議の場面、利用者と相手の役割、目的、制約を入力する
- 対応する参照資料を会議文脈へ追加する

### 会議中

- 自分と相手の発言をリアルタイムに表示する
- 必要なときに手動で返答提案を生成する
- 生成中の返答案をストリーミング表示する
- 返答案をコピーする
- ライブ支援用の別ウィンドウを表示する
- 自動生成を明示的に有効・無効にする（既定は無効）
- `間をつなぐ`などの提案モードを使う

### 会議後

- 保存された会議を一覧・詳細で確認する
- 会話ログ、保存された返答提案、利用可能な録音を確認する
- 会議タイトルを変更する
- 確認ダイアログから会議を削除する

### 音声認識

現在の設定で次のbackendを選択できます。

- Whisper（local。アプリ設定からmodelを準備可能）
- Vosk（local。アプリ設定からmodelを準備可能）
- Deepgram（cloud。credentialが必要）
- OpenAI（cloud。`OPENAI_API_KEY`が必要。発話単位で音声を転送）
- Grok / xAI（cloud。`XAI_API_KEY`が必要。ストリーミング音声を転送）
- Remote STT server
- Dummy（development/smoke用）

既定はWhisperです。modelの利用規約は各backendの提供元に従い、cloud backendの利用料金は各提供元で確認してください。

OpenAI、Grok / xAI、Deepgramは「音声」の「聞き取り方法」から選択します。クラウド方式を選ぶと、同じ画面のprovider固有controlでAPIキーの入力、保存状態、接続確認、変更、削除予定の指定を行えます。provider固有modelは詳細設定に残ります。OpenAIのcredential draftと状態は音声認識と返答支援で共有し、保存済みAPIキーの値は再表示しません。

## AIの利用方法

### 利用可能

- Gemini、OpenAI、Anthropicのcloud inferenceを利用者自身のcredentialで使う。APIキーは「支援方法」の対応するroute card内で入力・確認する。
- Ollamaのlocal/OpenAI-compatible endpointを使う。

保存済みcredentialの値は表示しません。provider固有model、endpoint、command、runtime診断の詳細は上級者向け設定です。Ollamaの接続先がloopback以外の場合、処理がこのPC内だけで完結するとは限りません。

### Experimental

- **Codex App Server直接経路**: 利用者環境の公式`codex`とChatGPT loginを使うAPIキー不要の試験提供経路です。generic ACPとは別の専用runtimeとして扱います。`codex-cli 0.144.0`を最低版とし、それ以降の安定版は起動時のprotocol検証を通れば利用できます。0.144.0 / 0.144.1はschema互換性を追跡する基準版で、未検証の新版は警告を表示します。利用者環境での実Codex turnとdesktop E2Eは未検証であり、一般提供済みとはみなしません。
- **Generic ACP経路**: 上級者向け設定だけで構成する別のexperimental runtimeです。Codex App Server経路のtransportとしては使わず、一般向けのCodex cardにACPを混在させません。

Experimental経路は環境、version、接続先により利用できない場合があります。CodexはGUIの`PATH`に加えて公式installerの標準配置先も探索し、initialize、ChatGPT認証、モデル一覧のtyped検証が`ready`のときだけ選択できます。返答開始とstream中にもthread/turn/notificationを検証し、非互換なら応答を終了してprocessを停止します。version probeがtimeoutした場合もその子processを停止します。未導入・最低版未満・未ログイン・モデル未提供は理由と復旧操作を区別し、インストールまたは更新後はアプリの再起動が必要です。これは実装上の境界であり、外部Codexを使った生成品質・対応OS・desktop E2Eの検証結果ではありません。

### Hosted service の境界

Meeting Supporterが運営するhosted serviceのserver実装・運用文書は、このOSSリポジトリに含まれません。通常のOSS buildではhosted serviceは未設定で利用できず、`not_offered`かつ`selectable = false`としてfail closedします。local STT、利用者自身のAPI credential、Ollama、Codex、ACPはhosted accountなしで利用できます。

## セットアップ

### 前提条件

- Node.js 20+
- Python 3.12–3.14（`.python-version`は検証済み最新の3.14を指定）
- Rust toolchain（Tauri desktop開発時）
- `uv`（ローカル開発時。配布版は初回起動時に公式配布物を取得）

選択するcloud STT/AI経路には各サービスのcredentialが必要です。local STT/AI経路には対応modelまたはlocal serviceが必要です。

### 依存関係

```bash
npm install
cd python && uv sync --locked
```

### Desktop development

```bash
npm run tauri dev
```

frontendだけを起動する場合:

```bash
npm run dev
```

Python backendだけを起動する場合:

```bash
npm run dev:python
```

### ローカル backend の境界

Tauri launcherがdesktop backendごとに生成するcapability tokenは、同一端末上のそのprocessへ届いた呼出しを確認するためだけのものです。これはhosted serviceの利用者認証ではありません。`npm run dev:python`で直接起動するPython backendと`python-server`は、ローカル開発・動作確認用であり、そのまま公開serviceとして運用することを想定していません。

### Build

```bash
npm run tauri build
```

### Release draft

公開用のタグと各manifestのversion、ライセンス、アイコン、production capabilityをまとめて検査します。

```bash
npm run check:release -- --tag v0.1.0
```

`package.json`、`package-lock.json`、`src-tauri/tauri.conf.json`、`src-tauri/Cargo.toml`、`python/pyproject.toml`、`openapi.json`のversionは同じ値にします。`v<version>`タグのpush、またはGitHub Actionsから`Release draft`を手動実行すると、Linux x64、Windows x64、macOS Apple Silicon / Intelのinstaller候補がdraft releaseへ追加されます。各artifactには`LICENSE`と`THIRD-PARTY-NOTICES.txt`を収録し、`uv`バイナリ自体は再配布しません。配布版は必要な場合だけ、初回起動時に`uv 0.11.7`を公式配布元から取得し、対象OS・architectureごとに固定したSHA-256を検証してからAppData配下へ展開します。

第三者ライセンス通知はlockfileから再生成し、差分と許可ポリシーをCIで検査します。

```bash
npm run licenses:generate
npm run licenses:check
```

利用者は設定の「このアプリ」からアプリ本体のGNU Affero General Public License v3.0と第三者ソフトウェア通知を確認できます。

workflowは公開を自動化しません。draftを公開する前に対象OSで起動を確認し、macOSはDeveloper ID署名とnotarization、WindowsはAuthenticode署名を完了してください。macOS用workflowは`APPLE_CERTIFICATE`、`APPLE_CERTIFICATE_PASSWORD`、`APPLE_SIGNING_IDENTITY`、`APPLE_ID`、`APPLE_PASSWORD`、`APPLE_TEAM_ID` secretsを受け取ります。Windows署名証明書の設定は配布主体が確定してから追加する必要があります。

## 設定とcredential

既定値は`python/config.default.toml`を参照してください。利用者設定はAppData配下の`config.toml`へ保存されます。

アプリ設定から保存したcredentialはPython `keyring`経由のOS credential storeを優先します。開発・CIでfile backendを明示する場合は`SECRET_STORE_BACKEND=file`を使用できます。credentialをissue、log、screenshot、文書へ記録しないでください。

「端末内・高精度」のWhisper modelは、アプリの音声設定からダウンロードできます。進捗表示と失敗時の再試行に対応し、保存先にはHugging Faceの標準共有cacheを使用するため、アプリ専用フォルダへmodelを重複保存しません。Whisperのダウンロードは途中キャンセルできません。

「端末内・軽量」のVosk音声認識データは、同じ画面から日本語（約48MB）または英語（約40MB）をダウンロードできます。進捗表示、キャンセル、失敗時の再試行に対応し、取得したデータはAppData配下の`models/speech`へ保存されます。既存のVosk modelを使う上級者は、詳細設定の`vosk_model_path`で展開済みディレクトリを指定できます。Ollamaの既定endpointは`http://localhost:11434/v1`です。

## Architecture and product authority

- [Documentation index](./doc/README.md)
- [Product Vision](./doc/product/vision.md)
- [Product Requirements and availability](./doc/product/prd.md)
- [Product Surfaces](./doc/ui/product-surfaces.md)
- [ADR-009: use-case/runtime/provider/config boundary](./doc/adr/009-live-reply-llm-usecase-runtime-provider-architecture.md)
- [ADR-010: AI route strategy](./doc/adr/010-ai-route-strategy.md)
- [ADR-011: general route card visibility and former Advanced boundary](./doc/adr/011-general-route-card-visibility.md)
- [ADR-012: native window chrome and pin preference](./doc/adr/012-native-window-chrome-and-pin-preference.md)
- [ADR-013: contextual API credential controls](./doc/adr/013-contextual-api-credential-controls.md)
- [ADR-015: localized UI message contract](./doc/adr/015-localized-ui-message-contract.md)

実装進捗と公開可能なbug・featureは[GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)で管理します。

## Main stack

| Layer     | Technology                               |
| --------- | ---------------------------------------- |
| UI        | React 19, TypeScript, Vite, Tailwind CSS |
| Desktop   | Tauri 2                                  |
| Backend   | Python 3.12–3.14, FastAPI, WebSocket     |
| Audio     | soundcard                                |
| Local STT | faster-whisper / Vosk                    |

## Contributing

外部コントリビューションは歓迎します。Pull Requestを送る前に[貢献ガイド](CONTRIBUTING.md)を確認してください。すべての人間のコントリビューターは[Contributor License Agreement](CLA.md)への同意が必要で、CLA Assistantの`license/cla`チェックが成功するまでマージしません。

## License

Copyright © 2026 Meeting Supporter contributors.

Meeting Supporter本体は[GNU Affero General Public License v3.0](LICENSE)（`AGPL-3.0-only`）で提供します。第三者ソフトウェアにはそれぞれのライセンスが適用され、詳細は[THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt)に収録しています。
