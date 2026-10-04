//! Credential checks are explicit user actions and never generate billable text.
use crate::{settings::Store, Error};
use futures_util::StreamExt;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{collections::HashSet, time::Duration};

const MODEL_LIST_TIMEOUT: Duration = Duration::from_secs(8);
const MAX_MODEL_LIST_BYTES: usize = 2 * 1024 * 1024;
const MAX_MODELS: usize = 2000;
const MAX_MODEL_PAGES: usize = 100;

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

    fn request(
        self,
        client: &reqwest::Client,
        url: &str,
        credential: &str,
        cursor: Option<&str>,
    ) -> Result<reqwest::RequestBuilder, super::AiError> {
        let mut url = reqwest::Url::parse(url).map_err(|_| super::AiError::Provider)?;
        if let Some(cursor) = cursor {
            let parameter = match self {
                Self::Anthropic => Some("after_id"),
                Self::Gemini => Some("pageToken"),
                Self::Openai => None,
            };
            if let Some(parameter) = parameter {
                url.query_pairs_mut().append_pair(parameter, cursor);
            }
        }
        let (_, _, header, prefix) = self.connection();
        let mut call = client
            .get(url)
            .header(header, format!("{prefix}{credential}"));
        if matches!(self, Self::Anthropic) {
            call = call.header("anthropic-version", "2023-06-01");
        }
        Ok(call)
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Request {
    provider: Provider,
    api_key: Option<String>,
}
pub(crate) async fn check(store: Store, request: Request) -> Result<Value, Error> {
    let (key, url, _, _) = request.provider.connection();
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
    let call = request.provider.request(&client, url, &credential, None)?;
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
    let (key, url, _, _) = provider.connection();
    let credential = tokio::task::spawn_blocking(move || store.secret(key))
        .await
        .map_err(|_| Error::Closed)??;
    let Some(credential) = credential.filter(|value| !value.trim().is_empty()) else {
        return Ok(
            json!({"ok":false,"provider":provider.id(),"models":[],"message":"APIキーを設定するとモデルを選択できます。"}),
        );
    };
    let client = reqwest::Client::builder()
        .timeout(MODEL_LIST_TIMEOUT)
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| super::AiError::Provider)?;
    // Keep the existing time budget for the whole catalog, including all pages.
    tokio::time::timeout(
        MODEL_LIST_TIMEOUT,
        fetch_models(&client, provider, url, &credential),
    )
    .await
    .map_err(|_| super::AiError::Provider)?
}

async fn fetch_models(
    client: &reqwest::Client,
    provider: Provider,
    url: &str,
    credential: &str,
) -> Result<Value, Error> {
    let mut models = Vec::new();
    let mut cursor = None::<String>;
    let mut seen_cursors = HashSet::new();
    let mut total_bytes = 0;
    for _ in 0..MAX_MODEL_PAGES {
        let response = match provider
            .request(client, url, credential, cursor.as_deref())?
            .send()
            .await
        {
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
            total_bytes += chunk.len();
            if total_bytes > MAX_MODEL_LIST_BYTES {
                return Err(super::AiError::Provider.into());
            }
            body.extend_from_slice(&chunk);
        }
        let page = parse_model_page(provider, &body)?;
        models.extend(page.models);
        if models.len() > MAX_MODELS {
            return Err(super::AiError::Provider.into());
        }
        let Some(next_cursor) = page.next_cursor else {
            let models = normalize_models(models);
            return Ok(json!({"ok":true,"provider":provider.id(),"models":models,"message":null}));
        };
        if !seen_cursors.insert(next_cursor.clone()) {
            return Err(super::AiError::Provider.into());
        }
        cursor = Some(next_cursor);
    }
    Err(super::AiError::Provider.into())
}

struct ModelPage {
    models: Vec<(String, String)>,
    next_cursor: Option<String>,
}

fn parse_model_page(provider: Provider, body: &[u8]) -> Result<ModelPage, Error> {
    #[derive(Deserialize)]
    struct DataResponse {
        data: Vec<DataModel>,
    }
    #[derive(Deserialize)]
    struct AnthropicResponse {
        data: Vec<DataModel>,
        has_more: bool,
        last_id: Option<String>,
    }
    #[derive(Deserialize)]
    struct DataModel {
        id: String,
        #[serde(default)]
        display_name: Option<String>,
    }
    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct GeminiResponse {
        #[serde(default)]
        models: Vec<GeminiModel>,
        next_page_token: Option<String>,
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
    let (models, next_cursor) = match provider {
        // https://developers.openai.com/api/reference/resources/models/methods/list
        // OpenAI returns a single list with no pagination parameters.
        Provider::Openai => {
            let response: DataResponse =
                serde_json::from_slice(body).map_err(|_| super::AiError::Provider)?;
            (
                response
                    .data
                    .into_iter()
                    .filter(|model| is_text_generation_model(provider, &model.id))
                    .map(|model| (model.id.clone(), model.id))
                    .collect(),
                None,
            )
        }
        // https://platform.claude.com/docs/en/api/models/list
        Provider::Anthropic => {
            let response: AnthropicResponse =
                serde_json::from_slice(body).map_err(|_| super::AiError::Provider)?;
            let next_cursor = if response.has_more {
                Some(
                    response
                        .last_id
                        .filter(|id| !id.trim().is_empty())
                        .ok_or(super::AiError::Provider)?,
                )
            } else {
                None
            };
            (
                response
                    .data
                    .into_iter()
                    .filter(|model| is_text_generation_model(provider, &model.id))
                    .map(|model| {
                        let label = model.display_name.unwrap_or_else(|| model.id.clone());
                        (model.id, label)
                    })
                    .collect(),
                next_cursor,
            )
        }
        // https://ai.google.dev/api/models#method:-models.list
        Provider::Gemini => {
            let response: GeminiResponse =
                serde_json::from_slice(body).map_err(|_| super::AiError::Provider)?;
            let models = response
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
                    if !is_text_generation_model(provider, &id) {
                        return None;
                    }
                    let label = if model.display_name.is_empty() {
                        id.clone()
                    } else {
                        model.display_name
                    };
                    Some((id, label))
                })
                .collect();
            (
                models,
                response.next_page_token.filter(|token| !token.is_empty()),
            )
        }
    };
    Ok(ModelPage {
        models,
        next_cursor,
    })
}

fn is_text_generation_model(provider: Provider, id: &str) -> bool {
    // OpenAI's list has no modality metadata; Gemini's generateContent also
    // covers image generation and TTS. Use known language-model families and
    // exclude specialized variants by ID, never by the human-readable label.
    // https://developers.openai.com/api/docs/models/all
    // https://ai.google.dev/gemini-api/docs/models
    let id = match provider {
        // Classify fine-tunes by the base model, not the user-defined suffix.
        Provider::Openai => id
            .strip_prefix("ft:")
            .and_then(|rest| rest.split(':').next())
            .unwrap_or(id),
        _ => id,
    };
    let known_family = match provider {
        Provider::Openai => {
            id.starts_with("gpt-")
                || id.starts_with("chatgpt-")
                || id.starts_with("codex-")
                || id == "chat-latest"
                || id.strip_prefix('o').is_some_and(|rest| {
                    let version = rest.split('-').next().unwrap_or("");
                    !version.is_empty() && version.bytes().all(|byte| byte.is_ascii_digit())
                })
        }
        Provider::Gemini => id.starts_with("gemini-") || id.starts_with("gemma-"),
        // Anthropic's catalog currently consists of Claude language models.
        // Do not require optional capabilities such as image input or thinking.
        Provider::Anthropic => id.starts_with("claude-"),
    };
    known_family
        && !id.split('-').any(|part| {
            matches!(
                part,
                "image"
                    | "audio"
                    | "tts"
                    | "live"
                    | "realtime"
                    | "transcribe"
                    | "transcription"
                    | "embedding"
                    | "embeddings"
                    | "moderation"
                    // Legacy OpenAI instruct models use the Completions API.
                    | "instruct"
            )
        })
}

fn normalize_models(mut models: Vec<(String, String)>) -> Vec<Value> {
    models.retain(|(id, label)| {
        !id.is_empty() && id.len() <= 256 && !label.is_empty() && label.chars().count() <= 256
    });
    models.sort_by(|left, right| left.1.cmp(&right.1).then(left.0.cmp(&right.0)));
    let mut seen_ids = HashSet::new();
    models
        .into_iter()
        .filter(|(id, _)| seen_ids.insert(id.clone()))
        .map(|(id, label)| json!({"id":id,"label":label}))
        .collect()
}

#[cfg(test)]
mod tests;
