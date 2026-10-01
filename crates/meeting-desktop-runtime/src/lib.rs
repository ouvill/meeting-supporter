//! In-process application composition with optional on-demand Python-only operations.
mod agents;
mod ai;
mod api;
mod cleanup;
mod interrupted;
pub mod models;
mod python_worker;
mod references;
mod runtime;
pub mod settings;
mod usage;
pub mod wire;

pub use api::Server;
use std::path::PathBuf;
use thiserror::Error;

#[derive(Clone)]
pub struct Config {
    pub agent_updates: bool,
    pub data_dir: PathBuf,
    pub audio_worker: PathBuf,
    pub speech_worker: PathBuf,
    pub model: Option<PathBuf>,
    pub legacy_model: Option<PathBuf>,
    pub hub_cache: PathBuf,
    pub hub_offline: bool,
    pub punctuation: Option<PathBuf>,
    pub python_worker: PathBuf,
}
#[derive(Debug, Error)]
pub enum Error {
    #[error(transparent)]
    Agent(#[from] agents::AgentError),
    #[error(transparent)]
    Model(#[from] models::ModelError),
    #[error("資料は10件まで、1件10 MiB・合計20 MiB以内で追加してください。")]
    ReferencesLimit,
    #[error("資料を読み込めませんでした。ファイルと設定を確認してください。")]
    References,
    #[error("削除条件が不正です。日付または最大容量を指定してください。")]
    CleanupPolicy,
    #[error("削除対象が変わりました。対象を再確認してください。")]
    CleanupChanged,
    #[error("関連ファイルを安全に削除できません。会議履歴を保持しています。")]
    CleanupFiles,
    #[error(transparent)]
    Ai(#[from] ai::AiError),
    #[error("設定値が不正です。入力内容を確認してください。")]
    Settings,
    #[error("OS の認証情報ストアを利用できません。")]
    Secrets,
    #[error("認証情報の復元に失敗しました。設定を確認してください。")]
    SecretsRollback,
    #[error("一部の記録を保存できませんでした。保存先の空き容量と状態を確認してください。保存済みの記録は保持しています。")]
    Storage(#[from] meeting_storage::StorageError),
    #[error("音声処理の停止を確認できませんでした。アプリを再起動してください。保存済みの記録は保持しています。")]
    ResourcesActive,
    #[error("同じ保存先を別のアプリが使用しています。先に起動したアプリを閉じてください。")]
    AlreadyRunning,
    #[error("音声処理に失敗しました。デバイスとモデルを確認してください。")]
    Media(#[from] meeting_media_runtime::wire::Error),
    #[error("会議の処理中です。完了を待ってください。")]
    Session(#[from] meeting_session::Error),
    #[error("ファイル操作に失敗しました。")]
    Io(#[from] std::io::Error),
    #[error("データ形式が不正です。")]
    Data(#[from] serde_json::Error),
    #[error("この操作は Rust バックエンドではまだ利用できません。")]
    Unsupported,
    #[error(
        "settings.toml の設定形式に対応していません。元のファイルを退避して設定し直してください。"
    )]
    SettingsVersion,
    #[error("音声認識を準備してから会議を開始してください。")]
    NotPrepared,
    #[error("会議中はこの操作を実行できません。")]
    Busy,
    #[error("会議が開始されていません。")]
    NoMeeting,
    #[error("音声認識の準備を中止しました。")]
    Cancelled,
    #[error("バックエンドを終了しています。")]
    Closed,
}
