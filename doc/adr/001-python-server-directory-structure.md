# ADR-001: Python サイドカーサーバーのディレクトリ構造

- **ステータス**: Accepted
- **日付**: 2026-04-25

## 背景

Tauri のサイドカーとして動作する Python FastAPI サーバーは、バイブコーディングによる機能追加の結果、以下の問題を抱えるようになった。

- `main.py` が 1000 行超のゴッドファイルになり、LLM エージェント定義・AppState・WebSocket エンドポイント・REST ルート・設定読み込みが混在している
- `app/` ディレクトリが空のまま放置されている（当初の設計意図が活かされていない）
- `services/` と `stt_v2/` は適切に分離されているが、`main.py` との境界が不明瞭
- `AudioLevelMonitor` と STT ストリームが別々に soundcard をオープンしている（二重オープン）
- 新しいエージェントや STT バックエンドを追加するたびに `main.py` の変更が必要

## 決定

**Pragmatic Layered Architecture（実用的な3層アーキテクチャ）** を採用する。
厳格な Clean Architecture（ports/adapters/use-cases）は採用しない。

理由: このアプリは「外部サービスのオーケストレーション」が主体（LLM API・STT バックエンド・WebSocket）であり、ドメインロジックは薄い。完全な CA はインダイレクションの儀式がコストに対してリターンが見合わない。

### 採用する構造

```
python/
├── main.py                          # エントリポイントのみ (~50行)
├── config.default.toml
├── pyproject.toml
│
├── app/
│   ├── lifespan.py                  # 起動/停止処理
│   │
│   ├── core/                        # 純粋な型・プロトコル・データクラス (I/O なし)
│   │   ├── types.py                 # JsonValue, TomlValue 等
│   │   ├── messages.py              # 送信メッセージの TypedDict 定義 (OutboundMessage union)
│   │   ├── protocols.py             # TurnLike, SttStreamLike 等の Protocol クラス
│   │   ├── models.py                # Turn dataclass, AgentDeps, _new_utterance_id()
│   │   ├── config.py                # LlmConfig, SttConfig, AudioConfig, AgentSettings
│   │   └── state.py                 # AppState dataclass (connections フィールドなし)
│   │
│   ├── services/                    # ステートフルサービス (core のみ import)
│   │   ├── broadcast.py             # BroadcastManager: 接続セット管理 + broadcast()
│   │   ├── config_loader.py         # ConfigLoader: TOML + 環境変数 → 型付き設定
│   │   ├── settings_store.py        # SettingsStore: TOML ファイル I/O
│   │   ├── context_loader.py        # load_context_files(path) -> str
│   │   ├── usage_logger.py          # LLM トークン使用量 JSONL ロガー
│   │   ├── conversation_orchestrator.py
│   │   ├── stt_controller.py
│   │   └── audio_level_monitor.py   # stt/ のパイプライン化後に廃止予定
│   │
│   ├── agents/                      # Pydantic AI エージェント定義
│   │   ├── prompts.py               # 全命令文字列定数
│   │   ├── tools.py                 # str_replace, search_context_files (ファクトリ関数)
│   │   └── factory.py               # build_agents() → AgentBundle
│   │
│   ├── api/                         # FastAPI ルーター
│   │   ├── system.py                # GET /health, GET /, GET /devices
│   │   ├── settings.py              # GET/POST /api/settings
│   │   ├── meeting.py               # POST /minutes
│   │   └── websocket.py             # WebSocket /ws: 接続管理 + 受信ループ + メッセージルーティング
│   │
│   └── stt/                         # stt_v2/ をリネーム + パイプライン化 (ADR-002)
│
└── tests/                           # src 構造を鏡映しを
    ├── conftest.py
    ├── app/
    │   ├── core/
    │   ├── services/
    │   ├── agents/
    │   ├── api/
    │   └── stt/
```

### 依存方向（下向きのみ）

```
main.py + app/lifespan.py
    ↓
app/api/  ←→  app/agents/
    ↓                ↓
    app/services/
         ↓
    app/stt/
         ↓
    app/core/
```

`app/core/` は何も import しない（stdlib のみ）。

### 主要な移行内容

| 現在 | 移行先 |
|------|--------|
| `main.py` — AppState クラス | `app/core/state.py` |
| `main.py` — connections set | `app/services/broadcast.py` (BroadcastManager) |
| `main.py` — 設定読み込みブロック | `app/services/config_loader.py` |
| `main.py` — 5 × Agent 定義 | `app/agents/factory.py` |
| `main.py` — ツール関数 | `app/agents/tools.py` |
| `main.py` — HTTP ルート | `app/api/system.py`, `settings.py`, `meeting.py` |
| `main.py` — WebSocket エンドポイント | `app/api/websocket.py` |
| `services/ws_message_dispatcher.py` | `app/api/websocket.py` に統合 (クラス不要) |
| `services/*.py` | `app/services/*.py` (import パス更新のみ) |
| `stt_v2/` | `app/stt/` (リネーム + ADR-002 の変更) |

### WebSocket 送信設計

送信経路は `BroadcastManager` に一本化する。サービス層が `ws.send_json()` を直接呼ぶことは禁止。

```python
# app/core/messages.py — 送信メッセージを TypedDict で型定義
class TranscriptionFinalMessage(TypedDict):
    type: Literal["transcription_final"]
    role: str
    text: str

OutboundMessage = TranscriptionFinalMessage | ReplyChunkMessage | AudioLevelMessage | ...

# app/services/broadcast.py — 2種類の送信メソッド
class BroadcastManager:
    async def broadcast(self, msg: OutboundMessage) -> None: ...  # 全クライアント
    async def reply(self, ws: WebSocket, msg: OutboundMessage) -> None: ...  # 送信元のみ
```

## 結果

**ポジティブ**
- `main.py` が純粋な配線コードになり、起動シーケンスが一望できる
- エージェント追加 → `app/agents/` のみ変更
- REST エンドポイント追加 → `app/api/` のみ変更
- STT バックエンド追加 → `app/stt/stages/` のみ変更（ADR-002）
- テストで `app.agents.factory.build_agents(state=fake_state)` と呼べるようになる

**トレードオフ**
- 移行作業中は import パスが混在する期間がある（段階的に移行する）
- ファイル数が増えるため、ディレクトリを横断した検索が必要になる場面がある
