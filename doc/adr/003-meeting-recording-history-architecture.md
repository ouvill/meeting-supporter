# ADR-003: ミーティング録音・履歴保存アーキテクチャ

- **ステータス**: Accepted
- **日付**: 2026-05-29

## 背景

現在の meeting-supporter は会議中の文字起こし・返答提案をリアルタイムに行えるが、会議終了後に内容を振り返る永続化機能がない。会議状態は `AppState.current_session` 上の `MeetingSession` にのみ保持され、プロセス終了やクラッシュで失われる。

音声キャプチャは ADR-002 の方針に従い `AudioPipeline` と `SttPipeline` に分離されている。

```text
CaptureStage → Q1 → Multiplexer
                    ├─ Qa → VolumeStage → WebSocket(audio_level)
                    └─ Qb → SttPipeline → ConversationOrchestrator
```

録音機能と履歴保存機能を追加するにあたり、以下を満たす必要がある。

- 会議履歴をアプリ再起動後も参照できる
- 録音ファイルを会議履歴と紐付けられる
- 既存のリアルタイム音声処理をブロックしない
- Tauri sidecar と standalone Python 実行の両方で保存先が安定する
- 将来の検索、議事録生成、エクスポート、同期に拡張できる

## 決定

ミーティング履歴は SQLite に保存し、録音音声はアプリデータディレクトリ配下のファイルとして保存する。DB には録音ファイルのメタデータと相対パスのみを保持する。

保存先は既存の `ConfigLoader.user_data_dir`（Tauri 実行時は `APP_DATA_DIR`、standalone 実行時は `platformdirs`）とする。

```text
<user_data_dir>/
  meeting_history.sqlite3
  recordings/
    <meeting_id>/
      other.wav
      self.wav
```

履歴保存・録音制御は Python バックエンドの責務とする。フロントエンドは API を通じて履歴一覧・詳細・録音再生を行う。

## 非目標

初期実装では以下を対象外とする。

- クラウド同期
- 外部カレンダー連携
- 音声ファイルの圧縮変換
- 録音ファイル内のタイムスタンプと文字起こしの厳密な同期表示
- 複数人の厳密な話者分離
- DB マイグレーションフレームワークの導入
- クラッシュした会議 (aborted) の UI 上での明示的な復旧フロー（DB 上には保存され、手動再開・削除は可能）
- 会議再開（resume）機能（一度 completed/aborted になった会議に追記はしない）

## モジュール構成

永続化用の DTO は `models.py` には置かず、`history_models.py` に分離する。`models.py` は従来通りランタイムモデル（`MeetingSession`, `Turn`, `ReplySuggestion`）を保持する。

```text
python/app/meetings/
  __init__.py
  history_models.py        # 永続化用 DTO: MeetingRecord, MeetingTurnRecord, ReplySuggestionRecord, RecordingAsset
  repository.py            # MeetingHistoryRepository Protocol
  sqlite_repository.py     # SQLite 実装
  service.py               # MeetingHistoryService
  recording.py             # RecordingService / RecorderSession
  lifecycle.py             # MeetingLifecycleCoordinator（開始/停止の順序調整のみ）
```

依存方向は以下の通り。Coordinator は「薄いオーケストレーション層」であり、具体的な振る舞いは持たずすべて配下に委譲する。

```text
MeetingLifecycleCoordinator（順序調整のみ）
  ├─ AppState（current_session 管理）
  ├─ SttController（STT 開始/停止）
  ├─ RecordingService（録音開始/停止、RecordingAsset 管理）
  ├─ MeetingHistoryService（会議メタデータ永続化）
  │   └─ MeetingHistoryRepository Protocol
  │       └─ SQLiteMeetingHistoryRepository
  └─ 状態通知（MeetingStateMsg 配信）
```

API layer は `MeetingLifecycleCoordinator` または `MeetingHistoryService` を通じて操作する。
ビジネスロジックは SQLite の詳細に直接依存させず、テストでは in-memory SQLite または fake repository を使えるようにする。

Coordinator が直接呼び出す永続化は会議の開始（`create_draft_meeting`）と終了（`complete_meeting`）に限定される。ターン単位の逐次永続化（`insert_turn`）や返答候補保存（`save_reply_suggestion`）は Coordinator の責務ではなく、`ConversationOrchestrator` および ReplyGenerator が `MeetingHistoryService` を直接呼び出す。

### 将来拡張: EventBus によるオプショナルな副作用

コードベースにはすでに `python/app/core/event_bus.py`（型安全な非同期 pub/sub）が存在する。初期実装では EventBus を使用せず Coordinator が直接サービスを呼ぶ。

将来的にオプショナルな副作用（解析ログ、クラウド同期、エクスポートなど）が増えた場合、以下のイベント型を追加して既存の EventBus で処理する。

- `MeetingStarted` / `MeetingCompleted` / `MeetingAborted`
- `TurnCommitted` / `ReplySuggestionGenerated`

ただし以下を原則とする。

- **必須の開始/停止ステップ**（AppState 設定、STT/録音開始停止、draft 作成/complete、状態通知）は Coordinator が直接呼び出し、EventBus 経由にはしない
- **EventBus は optional / best-effort な副作用に限定する**。ハンドラの失敗はログ記録のみで、メインのライフサイクルをブロックしない
- **必須の永続化**（ターン保存、会議メタデータ）は非同期 pub/sub に依存せず直接呼び出す

## ドメインモデル

### MeetingSession（`models.py`、既存）

会議中のインメモリ状態を表す。

```text
MeetingSession
- id / title / started_at / ended_at / turns / ai_note / is_active
```

### MeetingRecord（`history_models.py`）

永続化された会議履歴。`status` は会議のライフサイクル状態を追跡する。

```text
MeetingRecord
- id / title / started_at / ended_at / duration_seconds / ai_note
- status            # active | completed | aborted
- created_at / updated_at
```

会議開始時に `active` で作成、正常終了時に `completed`、クラッシュ検出時や中断時に `aborted` となる。

### MeetingTurnRecord（`history_models.py`）

```text
MeetingTurnRecord
- id / meeting_id / sequence / speaker / text / speaker_id / created_at
```

### ReplySuggestionRecord（`history_models.py`）

```text
ReplySuggestionRecord
- id / meeting_id / target_turn_id / sequence / agent_id / agent_label / text / created_at
```

`sequence` は表示順を安定させるために保持する。ランタイム上の `ReplySuggestion` モデルには持たせない。

### RecordingAsset（`history_models.py`）

```text
RecordingAsset
- id / meeting_id / role (other|self) / relative_path
- format / sample_rate / channels
- started_at / ended_at / size_bytes
```

録音ファイルは DB に保存せず、`user_data_dir` からの相対パスで参照する。

## SQLite スキーマ方針

### 初期化

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
```

`schema_version` テーブルで最低限のバージョン管理を行う。

```sql
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
```

### テーブル定義

```sql
CREATE TABLE meetings (
    id TEXT PRIMARY KEY,
    title TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds INTEGER,
    ai_note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'completed', 'aborted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE meeting_turns (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    speaker TEXT NOT NULL,
    text TEXT NOT NULL,
    speaker_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(meeting_id, sequence)
);

CREATE TABLE reply_suggestions (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    target_turn_id TEXT NOT NULL REFERENCES meeting_turns(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    agent_id TEXT NOT NULL,
    agent_label TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(target_turn_id, sequence)
);

CREATE TABLE recording_assets (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    format TEXT NOT NULL,
    sample_rate INTEGER NOT NULL,
    channels INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    size_bytes INTEGER,
    UNIQUE(meeting_id, role)
);
```

日時は ISO 8601 文字列として保存する。

### 削除セマンティクス

`DELETE /meetings/{meeting_id}` は以下を実行する。

1. DB 上の `meetings` 行を削除 — `ON DELETE CASCADE` により `meeting_turns`、`reply_suggestions`、`recording_assets` も自動削除される
2. `recordings/<meeting_id>/` ディレクトリとその全ファイルを物理削除

ファイル削除に失敗した場合でも DB 削除はロールバックしない。`DELETE` レスポンスで警告情報を返し、サーバーログにエラーを記録する。完全性が重要な場合は事後 cleanup スクリプトを実行可能とする。

## 履歴保存タイミング

**ターン単位の逐次永続化**を採用する。会議終了時の一括保存は行わない。

```text
【会議開始】
MeetingLifecycleCoordinator.start_meeting()
  ├─ AppState に current_session を設定
  ├─ MeetingHistoryService.create_draft_meeting() → status='active' で INSERT
  ├─ RecordingService.start() → RecordingStage が WAV 書き込みを開始
  ├─ SttController.start() → STT を開始
  └─ 状態通知で MeetingStateMsg(running=True) を配信

  ※ RecordingService.start を STT より先に実行し、STT 起動中の最初の音声を取り逃さない。
  ※ create_draft_meeting 成功後に STT/録音の起動に失敗した場合、draft を status='aborted' に更新し、
     開始済みのリソースを適切に解放する。

【発話確定時（ターン追加）】
ConversationOrchestrator.handle_speech()
  ├─ 従来通り current_session に Turn を追加
  ├─ WebSocket でフロントエンドへ配信
  └─ MeetingHistoryService.insert_turn(meeting_id, turn) → meeting_turns に INSERT

【返答候補生成時】
ConversationOrchestrator（または ReplyGenerator）
  └─ MeetingHistoryService.save_reply_suggestion(meeting_id, suggestion)

【会議終了】
MeetingLifecycleCoordinator.stop_meeting()
  ├─ SttController.stop() で STT を停止
  ├─ RecordingService.stop() で録音を停止し、RecordingAsset を確定
  ├─ MeetingHistoryService.complete_meeting()
  │   ├─ ended_at / duration_seconds を設定
  │   ├─ ai_note を保存
  │   ├─ recording_assets を INSERT
  │   └─ status = 'completed' に UPDATE
  ├─ 状態通知で MeetingStateMsg(running=False) を配信
  └─ AppState の current_session をクリア
```

### 利点

- **クラッシュ耐性**: 会議中のクラッシュでもそこまでの発話は DB に保存されている
- **復旧可能性**: 再起動時に `status='active'` のレコードを検出し、aborted としてマークできる
- **低レイテンシ**: 各操作は単一の INSERT/UPDATE で軽い
- **段階的可用性**: 会議が中断されても、そこまでの文字起こしは履歴として残る

## 録音アーキテクチャ

録音は `AudioPipeline` の `Multiplexer` から録音用キューへファンアウトする。

```text
CaptureStage → Q1 → Multiplexer
                    ├─ Qa → VolumeStage
                    ├─ Qb → SttPipeline
                    └─ Qc → RecordingStage
```

`AudioPipeline` はデバイス選択後に常時起動している。録音用 Qc は Multiplexer から常時接続され、RecordingStage は Qc を常時 drain する。会議中は drain したフレームを WAV ファイルに書き込み、非会議中は即座に破棄する。これにより Qc にフレームが蓄積することはない。録音開始時には念のため Qc を一度空にしてから、開始時刻以降のフレームのみを WAV に書き込む。

初期実装では `other` と `self` を別ファイルに保存する。

```text
recordings/<meeting_id>/other.wav
recordings/<meeting_id>/self.wav
```

録音形式は MVP では WAV とする（Python 標準ライブラリ `wave` で扱え、追加依存不要）。

### リアルタイム処理への影響防止

録音処理は専用キューと専用ステージで行い、音声キャプチャや STT から分離する。これにより録音処理の負荷が他経路に影響しない。

### 録音キュー方針（Queue Policy A: drop-new）

- **大容量 bounded queue**（例: 数千フレーム = 数十秒分のバッファ）
- **満杯時は drop-new**: 新しいフレームを切り捨て、警告ログを出力
- **`put_latest` は採用しない**: 古いフレーム破棄は録音の時系列連続性を損なう。chrono 順序維持には drop-new が適切
- ドロップ数が閾値を超えた場合、フロントエンドへ通知し録音品質低下を知らせる可能性を検討する

非会議中は RecordingStage が常時 Qc を消費して破棄するため、Qc にフレームが蓄積しない。満杯時 drop-new は会議中の WAV 書き込み遅延など一時的なボトルネック発生時の保護策として機能する。

この方針により、録音キューが詰まっても音声キャプチャや STT に backpressure はかからず、リアルタイム処理は継続される。

## API 設計

履歴管理 API を追加する。

```text
GET    /meetings                                    # 一覧（軽量レスポンス）
GET    /meetings/{meeting_id}                       # 詳細（発話・AIメモ・録音メタデータ）
PATCH  /meetings/{meeting_id}                       # タイトル更新（初期 scope）
DELETE /meetings/{meeting_id}                       # DB + ファイル削除

GET    /meetings/{meeting_id}/recordings
GET    /meetings/{meeting_id}/recordings/{role}
```

会議後の要約・議事録は、保存済みで`completed`かつ書き起こしがある会議に対して利用者が明示実行する。完了した全文だけを会議ごとのcanonical artifactとして保存し、partial、cancel、failureは保存しない。

```text
POST /meetings/{meeting_id}/minutes
```

OpenAPI から TypeScript クライアント生成済みのため、API 追加後は `npm run generate:api` を実行する。生成ファイル `src/api/generated` は手編集しない。

## フロントエンド構成

現在会議用の `MeetingScreen` と履歴閲覧用 UI を分ける。

```text
src/components/history/
  MeetingHistoryScreen.tsx
  MeetingHistoryList.tsx
  MeetingHistoryDetail.tsx
  RecordingPlayer.tsx

src/store/meetingHistoryStore.ts
```

履歴詳細では以下を表示する。

- タイトル / 開始・終了時刻 / 会議時間 / ステータス
- 文字起こし / AI メモ / 返答案
- 録音プレイヤー（単一の RecordingPlayer で other/self 両トラックを同時再生、共通の再生/停止/シーク/再生速度コントロールを備え、other/self の音量・ミュートは独立制御可能。時刻同期表示は後続フェーズ）

## 実装フェーズ

### Phase 1: 永続化基盤

- `app/meetings` パッケージ追加、`history_models.py` に DTO 定義
- SQLite repository 実装（WAL + foreign_keys + schema_version）、DB 初期化
- `MeetingHistoryService` 追加（create_draft, insert_turn, save_reply_suggestion, complete_meeting）
- `MeetingLifecycleCoordinator` 追加（開始/停止の順序調整のみ。ビジネスロジックは持たない）
- 会議開始時に status=active でレコード作成、ターン単位の逐次保存
- Python テスト追加

### Phase 2: 履歴 API

- GET/PATCH/DELETE `/meetings` 系エンドポイント
- OpenAPI 更新、TypeScript クライアント再生成

### Phase 3: 履歴 UI

- 一覧画面、詳細画面（transcript / AI メモ / 返答候補表示）
- 削除・タイトル変更

### Phase 4: 録音保存

- `RecordingService` / `RecordingStage` 追加（Queue Policy A: bounded + drop-new）
- `other.wav` / `self.wav` 保存、`RecordingAsset` を DB に保存

### Phase 5: 録音再生 UI

- 履歴詳細に RecordingPlayer 追加（other/self 両トラックの同時再生を標準とし、単一プレイヤー UI で共通の再生/停止/シーク/再生速度コントロールを提供）
- other/self それぞれに独立した音量スライダーとミュートトグルを配置
- role ごとの再生 API 接続・WAV ファイル取得

### 補足: 録音再生 UI の設計判断（2026-06-02）

初期検討では role ごとに独立した `<audio>` 要素を並べる方式を想定していたが、以下の理由から単一 RecordingPlayer による同時再生方式を採用する。

- **ユーザー体験**: 会議の録音を振り返る際、other/self を別々に再生するよりも同時に聞く方が会話の文脈を再現しやすい
- **UI の複雑性低減**: 独立プレイヤー方式では同期操作が複雑化する（両方を個別に再生開始する、再生位置を手動で合わせる等）。単一プレイヤーで共通の再生位置を持つことで自然な体験を提供する
- **将来の拡張性**: 同時再生を前提としたアーキテクチャは、将来的なミキシングダウンや話者分離表示への拡張が容易

## 影響とトレードオフ

### SQLite を選んだ理由

JSON ファイル保存より一覧・検索・削除・更新に強く、全文検索やタグ付けに拡張しやすい。

### 音声ファイルを DB 外に置く理由

音声はサイズが大きく、BLOB 保存はバックアップ・削除・再生・移行が重くなる。ファイル + DB メタデータの方が扱いやすい。

### ターン単位の逐次永続化を選んだ理由

会議終了時一括保存と比較して、クラッシュ耐性・応答軽さ・中断時の閲覧可能性・aborted 検出で優れる。書き込み回数増加は WAL モードで対処する。

### 録音キューを drop-new にした理由

`put_latest` は録音の時系列連続性を損なう。大容量 bounded + drop-new なら通常時は drop が発生せず、ボトルネック時も既存キュー内の音声を保護する。drop 検出時は警告を発行できる。

### WAV を選んだ理由

標準ライブラリで実装でき追加依存不要。ファイルサイズは大きいが MVP には十分。

### history_models.py を分離した理由

ランタイムモデルと永続化 DTO の責務を分離し、インポートパスから役割が自明になる。変更影響範囲も限定される。

## 検証方針

### Python

- repository CRUD テスト（in-memory SQLite）
- ターン逐次保存と会議完了フローの結合テスト
- 削除時 CASCADE の確認、およびファイル削除の振る舞い
- Coordinator が各コンポーネントを正しい順序で呼び出すことの検証
- 録音ステージの WAV 書き込みテスト
- status の CHECK 制約テスト、同一 meeting_id の重複 create_draft エラーハンドリング

### Frontend

- 履歴 store の reducer / fetch 処理テスト
- 履歴一覧・詳細の表示テスト
- 型チェックとビルド

## 未解決事項

- SQLite マイグレーションを自前実装にするか、将来ライブラリを導入するか
- 長時間録音時のファイルサイズ上限・自動チャンク分割
- 録音の自動削除・保持期間設定
- 文字起こしと録音再生位置の同期
- 会議再開（resume）機能の要否
- 履歴検索を通常 LIKE で始めるか、SQLite FTS を使うか
- aborted 状態の会議を UI 上でどう表示・管理するか
- 録音ファイル削除失敗時のリトライ戦略（バックグラウンド cleanup ジョブ）
