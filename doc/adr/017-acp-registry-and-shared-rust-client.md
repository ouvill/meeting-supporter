# ADR-017: Rust の外部エージェント接続を ACP と Registry に統一する

- **Status**: Accepted
- **Date**: 2026-09-30
- **Builds on**: ADR-009、ADR-010、ADR-011
- **Partially supersedes**: ADR-010 の Codex 専用 runtime・外部検出限定方針、および ADR-011 の外部エージェント構成方法を Rust 構成について置き換える

## Context

会話支援では、生成開始までの待ち時間と会議中の操作量を抑える必要がある。
Codex、Claude、Antigravity ごとに独自 protocol を追従すると、接続・認証・中断の実装が増える。
ACP Registry にはこれらの配布情報があり、共通 client と明示的な導入操作で扱える。

## Decision

- Rust の外部エージェント経路は公式 Rust ACP SDK に接続する。Codex は Registry の ACP adapter を使う。
- 設定の「支援方法」で Registry の検索、追加、認証、更新、削除を行う。返答案への割当は導入と分け、利用可能な経路だけ選択できる。
- Registry は公開された固定の HTTPS index を使う。導入済みエージェントがある場合、起動後に1日1回を目安に更新を自動確認し、最終確認時刻と結果を app-data に保存する。会議中は延期し、確認中に会議を開始した場合も確認を中断する。手動での一覧取得・更新確認も提供する。
- 配布形式は対応 platform の binary と、上限 version が明示された npm package を扱う。uvx、未対応の archive、terminal 認証は利用可能と表示しない。
- binary は app-data 内の一時ディレクトリに展開し、相対 path とサイズ制限を検証する。リンクは展開しない。提供された SHA-256 は照合する。
- npm は Registry の version を上限とする範囲で解決し、利用者の `min-release-age`・`before` 設定を維持する。script を無効にして app-data 内へ導入し、実際の package 名・version・entry point を検証する。導入した version は Registry の指定版と分けて保存・表示し、起動には node と検証済みの entry point を使用する。
- 更新確認は npm の lockfile のみを一時ディレクトリへ解決し、公開日制限と依存関係を満たす版だけを更新対象とする。確認ではエージェントを導入・起動しない。
- 導入と更新の適用は利用者が明示する。個別更新と「まとめて更新」を提供し、新版の導入・接続確認が成功してから manifest と接続を切り替える。失敗したエージェントは旧版を保持し、他の更新を継続する。自動適用は行わない。
- 更新件数と失敗理由は設定画面にだけ表示し、会話画面へ通知しない。アプリにはエージェント本体を同梱しない。
- 接続済み process を再利用し、返答ごとに session を分離する。上限で process を再作成し、中断・異常終了時はその接続を破棄する。
- 初期化、認証、session 作成、生成には期限を設ける。会議中は導入・更新・認証・削除を禁止する。
- ACP client は file / terminal capability を提供せず、permission request を拒否する。外部操作を求める返答は完了として保存しない。
- 認証情報はエージェント側が管理する。認証状態は実際の session 作成で確認し、Registry 掲載だけで利用可能とはしない。
- データ送信先、契約形態、料金は Registry 名から推測しない。費用は未確定として記録し、金額予算を設定している場合は生成を拒否する。
- エラーは固定の安全な文面へ変換し、protocol payload や stderr を UI・ログに出さない。

## Rejected Alternatives

- 各サービスの専用 protocol を Rust へ移植する: 追従箇所が増え、会話支援の共通機能に対する保守負担が大きい。
- 設定の command 入力だけで導入する: 配布物の選択と認証確認を利用者に委ねるため、一般設定での操作が複雑になる。
- Registry の全形式を自動実行する: 現在検証できる形式を越えるため、未対応形式は一覧で区別する。

## Consequences

接続と生成の共通実装を保ったままエージェントを追加できる。API 直接接続と Ollama は引き続き独立した経路として利用する。
エージェント内部の初期化や推論速度は ACP で短縮できるとは限らず、実サービスで認証・遅延・対応 OS の検証が必要である。
提供状態は experimental とし、Registry 掲載を品質保証と扱わない。

取得したエージェントは利用者権限で動く外部プログラムである。ACP capability を渡さないことは OS sandbox の代わりにはならない。
session にはアプリ所有の空ディレクトリを渡すが、エージェント固有の通信・設定・組込み tool の制限はそのエージェントに依存する。
checksum がない配布物は HTTPS と配布元に依存する。npm の依存解決、配布元の変更、互換性の継続検証は保守対象となる。
npm の公開日制限で旧版が選ばれた場合、Registry の起動引数との互換性は保証できないため、接続確認の結果を利用可能状態に反映する。
更新を確認しても同じ版が選ばれる場合がある。条件に合う版がない場合は制限を解除せず、失敗理由を表示する。

## Supersession

[ADR-010](./010-ai-route-strategy.md) の Codex direct と generic ACP の分離、Registry 導入を行わない判断を Rust 構成で置き換える。
[ADR-011](./011-general-route-card-visibility.md) の command を Advanced で構成する境界に、一般設定での Registry 導入・認証を追加する。
既存の Python 専用経路・保存設定を自動変換しない。hosted service の fail-closed、秘密情報の境界、正直な readiness 表示は維持する。

## Related Documents

- [Rust バックエンドの直接接続](../development/rust-desktop-backend.md)
- [Product Requirements](../product/prd.md)
- [Product Surfaces](../ui/product-surfaces.md)
- [ACP Registry と配布形式](https://github.com/agentclientprotocol/registry/blob/main/FORMAT.md)
- [ACP Rust SDK](https://agentclientprotocol.com/libraries/rust)
- [Zed の ACP 接続実装](https://github.com/zed-industries/zed/blob/main/crates/acp_thread/src/connection.rs)
