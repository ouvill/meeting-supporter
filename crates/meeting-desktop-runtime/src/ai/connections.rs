//! Credential checks are explicit user actions and never generate billable text.
use crate::{settings::Store, Error};
use serde::Deserialize;
use serde_json::{json, Value};
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Provider {
    Openai,
    Gemini,
    Anthropic,
    Deepgram,
    Xai,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Request {
    provider: Provider,
    api_key: Option<String>,
}
pub(crate) async fn check(store: Store, request: Request) -> Result<Value, Error> {
    let (key, url, header, prefix) = match request.provider {
        Provider::Openai => (
            "OPENAI_API_KEY",
            "https://api.openai.com/v1/models",
            "Authorization",
            "Bearer ",
        ),
        Provider::Gemini => (
            "GEMINI_API_KEY",
            "https://generativelanguage.googleapis.com/v1beta/models",
            "x-goog-api-key",
            "",
        ),
        Provider::Anthropic => (
            "ANTHROPIC_API_KEY",
            "https://api.anthropic.com/v1/models",
            "x-api-key",
            "",
        ),
        Provider::Deepgram => (
            "DEEPGRAM_API_KEY",
            "https://api.deepgram.com/v1/projects",
            "Authorization",
            "Token ",
        ),
        Provider::Xai => (
            "XAI_API_KEY",
            "https://api.x.ai/v1/models",
            "Authorization",
            "Bearer ",
        ),
    };
    let credential = match request.api_key.filter(|s| !s.is_empty()) {
        Some(s) => Some(s),
        None => tokio::task::spawn_blocking(move || store.secret(key))
            .await
            .map_err(|_| Error::Closed)??,
    };
    let Some(credential) = credential.filter(|s| !s.trim().is_empty()) else {
        return Ok(
            json!({"ok":false,"status":"invalid","message":"APIキーが設定されていません。"}),
        );
    };
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| super::AiError::Provider)?;
    let mut call = client
        .get(url)
        .header(header, format!("{prefix}{credential}"));
    if matches!(request.provider, Provider::Anthropic) {
        call = call.header("anthropic-version", "2023-06-01");
    }
    let (ok, status, message) = match call.send().await {
        Ok(r) if r.status().is_success() => (true, "verified", "接続を確認しました。"),
        Ok(r) if matches!(r.status().as_u16(), 401 | 403) => {
            (false, "invalid", "APIキーを確認してください。")
        }
        _ => (false, "unavailable", "サービスに接続できませんでした。"),
    };
    Ok(json!({"ok":ok,"status":status,"message":message}))
}
