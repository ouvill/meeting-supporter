# ADR-021: 旧 Python バックエンドを撤去する

- Status: Accepted
- Date: 2026-10-03

## 背景

Linux、Windows、macOS の標準構成で会議管理・音声・履歴・設定・AI 返答を Rust が所有する。
比較用バックエンドや起動時の環境準備を維持すると、標準構成と異なる依存・CI・開発手順が残る。

## 決定

- `python/`、旧クラウド音声認識用の `python-server/`、比較用テストと旧バックエンド専用 CI を削除する。
- Tauri は Rust バックエンドを必須依存とし、Python 起動・uv 取得・環境同期・認証情報の Python への転送を撤去する。
- Python から Rust を段階的に呼び出すための保存・会議・メディア用 JSONL CLI を削除し、ライブラリとして直接利用する。
- `python-worker/` は MarkItDown の DOCX 変換用として保持する。PyInstaller で同梱し、必要時だけ起動する。
- モデル取得・変換、ライセンス生成、CI の検証用 Python スクリプトは開発ツールとして保持する。
- Silero の組込みモデルは Rust 音声ワーカーの `resources/` に置き、従来と同じモデルとライセンスを維持する。
- 旧 Python だけが提供した Dummy 音声・WebRTC VAD・CUDA 固有の設定 UI を撤去し、Rust が受け付ける設定を表示する。
- 過去の設計判断と計測記録は履歴として残し、削除した実装の参照は固定コミットへ向ける。

## 結果

利用者による Python / uv のインストールは不要で、アプリの通常起動で Python プロセスを作らない。
CI は Rust ライブラリ、デスクトップ E2E、各 OS のインストーラー、MarkItDown worker を検証する。
保存済み会議・録音・設定や、開発者のローカル仮想環境はこの変更で削除しない。
