# meeting-desktop-runtime

既存の React 画面を使い、Tauri 内から会議管理・音声制御・履歴保存を直接実行する Rust ライブラリです。
起動手順と対応範囲は [Rust バックエンドの直接接続](../../doc/development/rust-desktop-backend.md)を参照してください。

## 所有する処理

- `meeting-session` の状態遷移に従った開始・停止・失敗時の保存判断。
- `meeting-storage` の SQLx repository。アプリの SQLite を直接使用します。
- 自分・相手それぞれの `meeting-media-runtime::Supervisor` と認識結果の保存。
- 既存 UI 用の認証付き loopback HTTP / WebSocket adapter。
- 既存 TOML 設定の検証・保存、認証情報ストアへの接続、利用量の記録と予算チェック。
- 資料の保存・返答への反映、前提資料フォルダの再読み込み。DOCX は共通 Python worker の MarkItDown に委譲します。
- 終了済み会議の期限・容量による削除対象確認と明示的な削除実行。
- Hugging Face 共有キャッシュを使う ReazonSpeech / Whisper のモデル取得・検証・キャンセル。Whisper.cpp Q8 推論を専用 worker で実行します。
- rig によるモデル API 接続と既存の返答生成・停止・保存。生成 ID と会議 ID により遅延した結果を分離します。

会議の変更操作は直列化します。音声結果の受信・保存は別タスクで継続し、
停止時には推論を drain、WAV を確定、音声プロセスを終了、結果の保存タスクを join してから会議を完了します。
DB の確定結果が不明な場合はファイルを消さず、未確定の会議として保持します。

モデル未準備でも両入力の capture を開き、音量メーターを更新します。
設定変更時は推論を無効化して入力監視を再開し、次の準備で保存値を渡します。
モデル準備はアプリ終了・準備解除で中断できます。通常の会議終了処理は UI の切断でキャンセルしません。
WebSocket は有限の送信キューを使用し、遅い接続を切断して snapshot で復元します。
再接続時の画面復元は直近 2,000 発言に制限し、全発言は SQLite の履歴に残します。

## 検証

以下は実マイク・実モデル・Python を使わず、Rust 製の合成 worker と一時 DB で実行できます。
`test-fixtures` はテスト用実行ファイルだけを追加する feature です。

```bash
cargo test --locked --manifest-path crates/meeting-desktop-runtime/Cargo.toml --features test-fixtures
cargo clippy --locked --manifest-path crates/meeting-desktop-runtime/Cargo.toml --all-targets --features test-fixtures -- -D warnings
```

両入力の末尾結果の保存、録音の Range 再生、履歴削除、推論障害時の未確定保持、
アプリ終了時の保存、準備の中断と子プロセス回収、再接続、二重開始、認証・Origin を検証します。
設定の引継ぎ・再起動後の復元・会議中の変更拒否・推論への反映、秘密値の非公開、
保存失敗時の認証情報の復元、準備前と終了後の音量通知も検証します。

ローカルの模擬 AI サーバーを使い、rig の各 provider の通信契約、返答の部分表示と保存、
停止・会議終了・途中切断、重複要求、複数スタイル、自動生成、経路設定の保存も検証します。
実 API キーや外部への推論リクエストは使用しません。

資料の DOCX 解析・サイズ制限・保存と返答への反映、前提資料の再読み込み、
削除対象の変化・UTC 境界・部分失敗・シンボリックリンクの拒否も一時データで検証します。

凍結した MarkItDown worker を使用する追加テストの起動方法は
[共通 Python worker](../../python-worker/README.md#検証)を参照してください。
