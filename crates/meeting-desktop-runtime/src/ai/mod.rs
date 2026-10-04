pub(crate) mod connections;
mod engine;
mod reply;
pub(crate) mod routes;
mod timing;
pub(crate) use engine::HttpClient;
pub(crate) use reply::Replies;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum AiError {
    #[error("AIの接続先・モデル・認証情報を設定してください。")]
    Configuration,
    #[error("このAI接続先はRustへの移植中です。別の接続先を選択してください。")]
    Unsupported,
    #[error("AIへ接続できませんでした。接続設定と認証情報を確認してください。")]
    Provider,
    #[error("AIの応答が完了しませんでした。再度お試しください。")]
    Incomplete,
    #[error("AIの応答待ちがタイムアウトしました。再度お試しください。")]
    Timeout,
    #[error("返答案を作れる発言がありません。")]
    NoTarget,
    #[error("返答生成が無効です。設定を確認してください。")]
    Disabled,
    #[error("AI利用量の予算上限に達したか、利用量を確認できません。")]
    Budget,
    #[error("返答案を保存できませんでした。文字起こしと会議の記録は継続します。")]
    Persistence,
    #[error("生成が多すぎます。完了を待つか停止してください。")]
    Busy,
}
