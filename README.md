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
- 保存済みの議事録を閲覧する
- 会議タイトルを変更する
- 確認ダイアログから会議を削除する

### 音声認識

現在の設定で次のbackendを選択できます。

- Whisper（local。アプリ設定からmodelを準備可能）
- ReazonSpeech K2-v2 int8（local・日本語専用。アプリ設定からmodelを準備可能）
- Dummy（development/smoke用）

既定はReazonSpeechです。モデルの利用規約は各提供元に従います。

VADエンジンはSTT backendとは独立して、Silero VADまたはWebRTC VADから選択します。


## AIの利用方法

### 利用可能

- Gemini、OpenAI、Anthropicのcloud inferenceを利用者自身のcredentialで使う。APIキーは「支援方法」の対応するroute card内で入力・確認する。
- Ollamaのlocal/OpenAI-compatible endpointを使う。

保存済みcredentialの値は表示しません。provider固有model、endpointの詳細は上級者向け設定です。Ollamaの接続先がloopback以外の場合、処理がこのPC内だけで完結するとは限りません。

### Experimental

Rust 構成では、設定の「支援方法」から ACP Registry のエージェントを追加・認証し、返答案へ割り当てられます。
Codex、Claude、Antigravity を共通の ACP client で扱います。配布形式の条件と未検証事項は
[Rust バックエンドの直接接続](doc/development/rust-desktop-backend.md#acp-エージェント)を参照してください。
### Hosted service の境界

Meeting Supporterが運営するhosted serviceのserver実装・運用文書は、このOSSリポジトリに含まれません。通常のOSS buildではhosted serviceは未設定で利用できず、`not_offered`かつ`selectable = false`としてfail closedします。local STT、利用者自身のAPI credential、Ollama、ACPエージェントはhosted accountなしで利用できます。

## セットアップ

### 前提条件

- Node.js 20+
- Python 3.12–3.14（旧 Python バックエンドを開発する場合）
- Rust toolchain（Tauri desktop開発時）
- `uv`（DOCX worker のビルド、または旧 Python バックエンドの開発時）

Linux / Windows の標準構成は Rust を使用します。配布版には必要な worker を同梱し、利用者環境の Python / uv は使いません。
ビルド環境と CI の検証範囲は [Rust のインストーラー](doc/development/rust-desktop-backend.md#linux--windows-のインストーラー)を参照してください。
macOS は引き続き旧 Python 構成です。

返答生成のcloud AI経路には各サービスのcredentialが必要です。ローカル音声認識には対応モデル、ローカルAIにはサービスの準備が必要です。

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

### Rust ローカル文字起こし

`npm run dev:rust` で、既存画面から Tauri 内の Rust バックエンドを直接利用できます。
起動前に音声取得・推論 worker を差分ビルドするため、個別のビルド操作は不要です。
Python を起動しない経路の手順と制約は [Rust バックエンドの直接接続](doc/development/rust-desktop-backend.md)を参照してください。

既存 Python 経路の ReazonSpeech 音声認識だけを Rust worker に切り替えることもできます。
起動方法と現在の範囲は [Rust ローカル文字起こし](doc/development/rust-local-speech.md)を参照してください。

独立した `dev:native` 画面は音声処理の検証用です。既存アプリへの接続には上記の手順を使います。

### 紹介サイト

製品紹介用の静的サイトは`website/`にあります。

```bash
npm run site:dev
npm run site:build
```

production buildは`dist-website/`へ出力されます。

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

Rust 構成は AppData 配下の `settings.toml` を使用し、ファイル全体に `schema_version = 1` を持たせます。旧設定からは自動移行しません。設定の例と再設定手順は [Rust の設定手順](doc/development/rust-desktop-backend.md#設定の保存と反映)を参照してください。

Python 構成は `python/config.default.toml` の既定値と AppData 配下の `config.toml` を使用します。

Rust 構成の API キーは OS の認証情報ストアへ保存します。Python 構成は Python `keyring` を使用し、開発・CI では `SECRET_STORE_BACKEND=file` を指定できます。credential を issue、log、screenshot、文書へ記録しないでください。

「端末内・高精度」のWhisper modelは、アプリの音声設定からダウンロードできます。進捗表示と失敗時の再試行に対応し、保存先にはHugging Faceの標準共有cacheを使用するため、アプリ専用フォルダへmodelを重複保存しません。Pythonバックエンドでは途中キャンセルできません。Rustバックエンドでは取得のキャンセルにも対応します。

「端末内・日本語高精度」のReazonSpeech K2-v2 int8を音声認識の既定方式として使用します。modelは同じ画面からダウンロードでき、約153MBを使用してHugging Faceの標準共有cacheへ保存します。日本語の音声だけに対応し、1回の認識区間をmodelの上限である約30秒未満に分割します。Pythonバックエンドでは途中キャンセルできません。Rustバックエンドでは取得のキャンセルにも対応します。modelとReazonSpeechの利用条件はApache License 2.0です。

Ollamaの既定endpointは`http://localhost:11434/v1`です。

声の検出は既定でSilero VADを使用します。Torchは導入せず、同梱した約208KBのint8 ONNX modelをONNX Runtimeで直接実行します。処理は端末内で完結し、最小負荷を優先する場合は音声設定からWebRTC VADへ切り替えられます。Silero VAD modelの利用条件はMIT Licenseです。

音声デバイス、VAD、音声認識方式の設定は会議中には変更できず、進行中の会議は開始時のaudio runtimeを使い続けます。会議停止中にこれらの変更を保存すると、アプリ本体を再起動せずに音声subsystem全体を新設定で再生成します。stage単位のhot-swapは行わず、再生成に失敗した場合は変更前のaudio runtimeへ戻します。会議中に外部からconfig変更通知を受けた場合も、その会議の終了後まで再読み込みを保留します。

## Architecture and product authority

- [Documentation index](./doc/README.md)
- [Rust 音声コアの試作・移行検討](./test/rust-audio-core/README.md)（experimental。本番には未接続）
- [Rust 音声バックエンドの移行試作](./test/rust-native-backend/README.md)（experimental。Silero + ReazonSpeech によるマイク / PCM / WAV の文字起こし）
- [Product Vision](./doc/product/vision.md)
- [Product Requirements and availability](./doc/product/prd.md)
- [Product Surfaces](./doc/ui/product-surfaces.md)
- [ADR-009: use-case/runtime/provider/config boundary](./doc/adr/009-live-reply-llm-usecase-runtime-provider-architecture.md)
- [ADR-010: AI route strategy](./doc/adr/010-ai-route-strategy.md)
- [ADR-011: general route card visibility and former Advanced boundary](./doc/adr/011-general-route-card-visibility.md)
- [ADR-012: native window chrome and pin preference](./doc/adr/012-native-window-chrome-and-pin-preference.md)
- [ADR-013: contextual API credential controls](./doc/adr/013-contextual-api-credential-controls.md)
- [ADR-015: localized UI message contract](./doc/adr/015-localized-ui-message-contract.md)
- [ADR-016: Rust runtime and ownership boundaries](./doc/adr/016-rust-runtime-and-ownership-boundaries.md)（Proposed）

実装進捗と公開可能なbug・featureは[GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)で管理します。

## Main stack

| Layer     | Technology                                 |
| --------- | ------------------------------------------ |
| UI        | React 19, TypeScript, Vite, Tailwind CSS   |
| Desktop   | Tauri 2                                    |
| Backend   | Python 3.12–3.14, FastAPI, WebSocket       |
| Audio     | soundcard / Silero VAD / WebRTC VAD        |
| Local STT | faster-whisper / ReazonSpeech K2-v2 |

## Contributing

外部コントリビューションは歓迎します。Pull Requestを送る前に[貢献ガイド](CONTRIBUTING.md)を確認してください。すべての人間のコントリビューターは[Contributor License Agreement](CLA.md)への同意が必要で、CLA Assistantの`license/cla`チェックが成功するまでマージしません。

## License

Copyright © 2026 Meeting Supporter contributors.

Meeting Supporter本体は[GNU Affero General Public License v3.0](LICENSE)（`AGPL-3.0-only`）で提供します。第三者ソフトウェアにはそれぞれのライセンスが適用され、詳細は[THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt)に収録しています。
