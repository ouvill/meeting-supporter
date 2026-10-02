//! Credential checks are explicit user actions and never generate billable text.
use crate::{settings::Store, Error};
use futures_util::StreamExt;
use serde::Deserialize;
use serde_json::{json, Value};
#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Provider {
    Openai,
    Gemini,
    Anthropic,
}

impl Provider {
    fn connection(self) -> (&'static str, &'static str, &'static str, &'static str) {
        match self {
            Self::Openai => (
                "OPENAI_API_KEY",
                "https://api.openai.com/v1/models",
                "Authorization",
                "Bearer ",
            ),
            Self::Gemini => (
                "GEMINI_API_KEY",
                "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
                "x-goog-api-key",
                "",
            ),
            Self::Anthropic => (
                "ANTHROPIC_API_KEY",
                "https://api.anthropic.com/v1/models?limit=1000",
                "x-api-key",
                "",
            ),
        }
    }

    fn id(self) -> &'static str {
        match self {
            Self::Openai => "openai",
            Self::Gemini => "gemini",
            Self::Anthropic => "anthropic",
        }
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Request {
    provider: Provider,
    api_key: Option<String>,
}
pub(crate) async fn check(store: Store, request: Request) -> Result<Value, Error> {
    let (key, url, header, prefix) = request.provider.connection();
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

pub(crate) async fn models(store: Store, provider: Provider) -> Result<Value, Error> {
    let (key, url, header, prefix) = provider.connection();
    let credential = tokio::task::spawn_blocking(move || store.secret(key))
        .await
        .map_err(|_| Error::Closed)??;
    let Some(credential) = credential.filter(|value| !value.trim().is_empty()) else {
        return Ok(
            json!({"ok":false,"provider":provider.id(),"models":[],"message":"APIキーを設定するとモデルを選択できます。"}),
        );
    };
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| super::AiError::Provider)?;
    let mut call = client
        .get(url)
        .header(header, format!("{prefix}{credential}"));
    if matches!(provider, Provider::Anthropic) {
        call = call.header("anthropic-version", "2023-06-01");
    }
    let response = match call.send().await {
        Ok(response) if response.status().is_success() => response,
        Ok(response) if matches!(response.status().as_u16(), 401 | 403) => {
            return Ok(
                json!({"ok":false,"provider":provider.id(),"models":[],"message":"APIキーを確認してください。"}),
            );
        }
        _ => {
            return Ok(
                json!({"ok":false,"provider":provider.id(),"models":[],"message":"モデル一覧を取得できませんでした。"}),
            );
        }
    };
    let mut stream = response.bytes_stream();
    let mut body = Vec::new();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| super::AiError::Provider)?;
        if body.len() + chunk.len() > 2 * 1024 * 1024 {
            return Err(super::AiError::Provider.into());
        }
        body.extend_from_slice(&chunk);
    }
    let models = parse_models(provider, &body)?;
    Ok(json!({"ok":true,"provider":provider.id(),"models":models,"message":null}))
}

fn parse_models(provider: Provider, body: &[u8]) -> Result<Vec<Value>, Error> {
    #[derive(Deserialize)]
    struct DataResponse {
        data: Vec<DataModel>,
    }
    #[derive(Deserialize)]
    struct DataModel {
        id: String,
        #[serde(default)]
        display_name: Option<String>,
    }
    #[derive(Deserialize)]
    struct GeminiResponse {
        models: Vec<GeminiModel>,
    }
    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct GeminiModel {
        name: String,
        #[serde(default)]
        display_name: String,
        #[serde(default)]
        supported_generation_methods: Vec<String>,
    }
    let mut models: Vec<(String, String)> = match provider {
        Provider::Openai | Provider::Anthropic => {
            let response: DataResponse =
                serde_json::from_slice(body).map_err(|_| super::AiError::Provider)?;
            response
                .data
                .into_iter()
                .map(|model| {
                    let label = model.display_name.unwrap_or_else(|| model.id.clone());
                    (model.id, label)
                })
                .collect()
        }
        Provider::Gemini => {
            let response: GeminiResponse =
                serde_json::from_slice(body).map_err(|_| super::AiError::Provider)?;
            response
                .models
                .into_iter()
                .filter(|model| {
                    model
                        .supported_generation_methods
                        .iter()
                        .any(|method| method == "generateContent")
                })
                .filter_map(|model| {
                    let id = model.name.strip_prefix("models/")?.to_owned();
                    let label = if model.display_name.is_empty() {
                        id.clone()
                    } else {
                        model.display_name
                    };
                    Some((id, label))
                })
                .collect()
        }
    };
    models.retain(|(id, label)| {
        !id.is_empty() && id.len() <= 256 && !label.is_empty() && label.len() <= 256
    });
    models.sort_by(|left, right| left.1.cmp(&right.1).then(left.0.cmp(&right.0)));
    models.dedup_by(|left, right| left.0 == right.0);
    if models.len() > 2000 {
        return Err(super::AiError::Provider.into());
    }
    Ok(models
        .into_iter()
        .map(|(id, label)| json!({"id":id,"label":label}))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn provider_model_lists_are_typed_filtered_and_normalized() {
        let openai = parse_models(
            Provider::Openai,
            br#"{"data":[{"id":"gpt-z"},{"id":"gpt-a"}]}"#,
        )
        .unwrap();
        assert_eq!(openai[0], json!({"id":"gpt-a","label":"gpt-a"}));

        let anthropic = parse_models(
            Provider::Anthropic,
            br#"{"data":[{"id":"claude-a","display_name":"Claude A"}]}"#,
        )
        .unwrap();
        assert_eq!(anthropic, vec![json!({"id":"claude-a","label":"Claude A"})]);

        let gemini = parse_models(
            Provider::Gemini,
            br#"{"models":[{"name":"models/gemini-chat","displayName":"Gemini Chat","supportedGenerationMethods":["generateContent"]},{"name":"models/gemini-embed","displayName":"Embedding","supportedGenerationMethods":["embedContent"]}]}"#,
        )
        .unwrap();
        assert_eq!(
            gemini,
            vec![json!({"id":"gemini-chat","label":"Gemini Chat"})]
        );
        assert!(parse_models(Provider::Openai, br#"{"models":[]}"#).is_err());
    }
}
