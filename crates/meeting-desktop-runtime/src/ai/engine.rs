//! Rig is the provider adapter; meeting ownership stays in the application.
use super::{
    routes::{Provider, Route},
    AiError,
};
use rig::{
    client::CompletionClient, completion::CompletionModel, providers,
    streaming::StreamingCompletionResponse,
};
use std::time::Duration;

pub(super) async fn stream(
    route: &Route,
    instruction: String,
    prompt: String,
) -> Result<StreamingCompletionResponse, AiError> {
    let http = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(90))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| AiError::Configuration)?;
    macro_rules! request {
        ($client:expr) => {{
            $client
                .map_err(|_| AiError::Configuration)?
                .completion_model(&route.model)
                .completion_request(prompt)
                .preamble(instruction)
                .max_tokens(1024)
                .stream()
                .await
                .map_err(|_| AiError::Provider)
        }};
    }
    let key = route.key.as_deref().unwrap_or("unused");
    match route.provider {
        Provider::Openai => request!(providers::openai::Client::builder()
            .api_key(key)
            .base_url(&route.base_url)
            .http_client(http)
            .build()),
        Provider::Gemini => request!(providers::gemini::Client::builder()
            .api_key(key)
            .base_url(&route.base_url)
            .http_client(http)
            .build()),
        Provider::Anthropic => request!(providers::anthropic::Client::builder()
            .api_key(key)
            .base_url(&route.base_url)
            .http_client(http)
            .build()),
        // Preserve existing Ollama /v1 settings and their OpenAI-compatible API contract.
        Provider::Ollama => request!(providers::openai::CompletionsClient::builder()
            .api_key(key)
            .base_url(&route.base_url)
            .http_client(http)
            .build()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{
        extract::State,
        http::{HeaderMap, StatusCode, Uri},
        routing::post,
        Json, Router,
    };
    use serde_json::Value;
    use std::sync::{Arc, Mutex};
    type Captured = Arc<Mutex<Option<(Uri, HeaderMap, Value)>>>;
    async fn reject(
        State(capture): State<Captured>,
        uri: Uri,
        headers: HeaderMap,
        Json(body): Json<Value>,
    ) -> StatusCode {
        *capture.lock().unwrap() = Some((uri, headers, body));
        StatusCode::UNAUTHORIZED
    }
    #[tokio::test]
    async fn rig_adapters_use_provider_protocols_and_redact_errors() {
        for (provider, path, prefix, header) in [
            (Provider::Openai, "/v1/responses", "/v1", "authorization"),
            (
                Provider::Ollama,
                "/v1/chat/completions",
                "/v1",
                "authorization",
            ),
            (
                Provider::Gemini,
                "/v1beta/models/synthetic-model:streamGenerateContent",
                "",
                "x-goog-api-key",
            ),
            (Provider::Anthropic, "/v1/messages", "", "x-api-key"),
        ] {
            let captured: Captured = Arc::new(Mutex::new(None));
            let router = Router::new()
                .route(path, post(reject))
                .with_state(captured.clone());
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let base = format!(
                "http://127.0.0.1:{}{prefix}",
                listener.local_addr().unwrap().port()
            );
            let server = tokio::spawn(async move {
                axum::serve(listener, router).await.unwrap();
            });
            let route = Route {
                provider,
                model: "synthetic-model".into(),
                base_url: base,
                key: Some("synthetic-credential".into()),
            };
            let result = stream(
                &route,
                "synthetic instruction".into(),
                "synthetic prompt".into(),
            )
            .await;
            // Some adapters open the HTTP request on the first stream poll.
            let rejected = match result {
                Err(AiError::Provider) => true,
                Ok(mut response) => {
                    use futures_util::StreamExt;
                    matches!(response.next().await, Some(Err(_)))
                }
                Err(_) => false,
            };
            assert!(
                rejected,
                "provider adapter at {path} must reject unauthorized responses"
            );
            let (uri, headers, body) = captured
                .lock()
                .unwrap()
                .take()
                .expect("adapter must use expected API path");
            assert_eq!(uri.path(), path);
            if provider == Provider::Gemini {
                assert!(uri
                    .query()
                    .unwrap_or("")
                    .contains("key=synthetic-credential"));
            } else {
                assert!(headers[header]
                    .to_str()
                    .unwrap()
                    .contains("synthetic-credential"));
            }
            assert!(body.to_string().contains("synthetic prompt"));
            assert!(body.to_string().contains("synthetic instruction"));
            assert!(!AiError::Provider
                .to_string()
                .contains("synthetic-credential"));
            server.abort();
            let _ = server.await;
        }
    }
}
