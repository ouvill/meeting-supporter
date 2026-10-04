use super::*;
use axum::{
    body::Bytes,
    extract::State,
    http::{HeaderMap, Method, StatusCode, Uri},
    routing::get,
    Json, Router,
};
use std::{
    collections::{HashMap, VecDeque},
    sync::{Arc, Mutex},
};

struct CapturedRequest {
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Bytes,
}

#[derive(Clone)]
struct ServerState {
    responses: Arc<Mutex<VecDeque<(StatusCode, Value)>>>,
    requests: Arc<Mutex<Vec<CapturedRequest>>>,
}

async fn serve_page(
    State(state): State<ServerState>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Bytes,
) -> (StatusCode, Json<Value>) {
    state.requests.lock().unwrap().push(CapturedRequest {
        method,
        uri,
        headers,
        body,
    });
    let (status, body) = state
        .responses
        .lock()
        .unwrap()
        .pop_front()
        .unwrap_or((StatusCode::INTERNAL_SERVER_ERROR, json!({})));
    (status, Json(body))
}

async fn fetch(
    provider: Provider,
    responses: Vec<(StatusCode, Value)>,
) -> (Result<Value, Error>, Vec<CapturedRequest>) {
    let state = ServerState {
        responses: Arc::new(Mutex::new(responses.into())),
        requests: Arc::new(Mutex::new(Vec::new())),
    };
    let router = Router::new()
        .fallback(get(serve_page))
        .with_state(state.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut url = reqwest::Url::parse(provider.connection().1).unwrap();
    url.set_scheme("http").unwrap();
    url.set_host(Some("127.0.0.1")).unwrap();
    url.set_port(Some(listener.local_addr().unwrap().port()))
        .unwrap();
    let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();
    let result = fetch_models(&client, provider, url.as_str(), "synthetic-credential").await;
    server.abort();
    let _ = server.await;
    let requests = std::mem::take(&mut *state.requests.lock().unwrap());
    (result, requests)
}

fn query(request: &CapturedRequest) -> HashMap<String, String> {
    reqwest::Url::parse(&format!("http://localhost{}", request.uri))
        .unwrap()
        .query_pairs()
        .into_owned()
        .collect()
}

fn assert_request(request: &CapturedRequest, path: &str, header: &str, credential: &str) {
    assert_eq!(request.method, Method::GET);
    assert_eq!(request.uri.path(), path);
    assert!(request.body.is_empty());
    assert_eq!(request.headers[header], credential);
    assert!(!request.uri.to_string().contains("synthetic-credential"));
}

#[tokio::test]
async fn openai_lists_only_text_models_with_a_single_bearer_authenticated_request() {
    let (result, requests) = fetch(
        Provider::Openai,
        vec![(
            StatusCode::OK,
            json!({"object":"list","data":[
                {"id":"gpt-5.4-mini","object":"model","created":123,"owned_by":"openai","shutdown_date":null},
                {"id":"ft:gpt-4.1:synthetic:custom:id","object":"model","created":123,"owned_by":"organization"},
                {"id":"gpt-4.1","object":"model","created":123,"owned_by":"openai"},
                {"id":"gpt-image-1"},
                {"id":"text-embedding-3-small"},
                {"id":"gpt-4o-mini-transcribe"}
            ]}),
        )],
    )
    .await;
    assert_eq!(
        result.unwrap(),
        json!({"ok":true,"provider":"openai","message":null,"models":[
            {"id":"ft:gpt-4.1:synthetic:custom:id","label":"ft:gpt-4.1:synthetic:custom:id"},
            {"id":"gpt-4.1","label":"gpt-4.1"},
            {"id":"gpt-5.4-mini","label":"gpt-5.4-mini"}
        ]})
    );
    assert_eq!(requests.len(), 1);
    assert_request(
        &requests[0],
        "/v1/models",
        "authorization",
        "Bearer synthetic-credential",
    );
    assert!(requests[0].uri.query().is_none());
    assert!(!requests[0].headers.contains_key("anthropic-version"));
}

#[tokio::test]
async fn anthropic_follows_last_id_and_deduplicates_across_all_pages() {
    let (result, requests) = fetch(
        Provider::Anthropic,
        vec![
            (
                StatusCode::OK,
                json!({"data":[
                {"id":"claude-z","display_name":"Zulu"},
                {"id":"claude-shared","display_name":"Charlie"},
                {"id":"synthetic-embedding","display_name":"Embedding"}
            ],"has_more":true,"first_id":"claude-z","last_id":"synthetic-embedding"}),
            ),
            (
                StatusCode::OK,
                json!({"data":[
                {"id":"claude-a","display_name":"Alpha"},
                {"id":"claude-shared","display_name":"Beta"},
                {"id":"claude-b","display_name":"Bravo"}
            ],"has_more":true,"first_id":"claude-a","last_id":"claude-b"}),
            ),
            (
                StatusCode::OK,
                json!({"data":[
                {"id":"claude-c","display_name":"Delta"}
            ],"has_more":false,"first_id":"claude-c","last_id":"claude-c"}),
            ),
        ],
    )
    .await;
    assert_eq!(
        result.unwrap()["models"],
        json!([
            {"id":"claude-a","label":"Alpha"},
            {"id":"claude-shared","label":"Beta"},
            {"id":"claude-b","label":"Bravo"},
            {"id":"claude-c","label":"Delta"},
            {"id":"claude-z","label":"Zulu"}
        ])
    );
    assert_eq!(requests.len(), 3);
    for (request, cursor) in
        requests
            .iter()
            .zip([None, Some("synthetic-embedding"), Some("claude-b")])
    {
        assert_request(request, "/v1/models", "x-api-key", "synthetic-credential");
        assert_eq!(request.headers["anthropic-version"], "2023-06-01");
        let params = query(request);
        assert_eq!(params.get("limit").map(String::as_str), Some("1000"));
        assert_eq!(params.get("after_id").map(String::as_str), cursor);
        assert_eq!(params.len(), if cursor.is_some() { 2 } else { 1 });
    }
}

#[tokio::test]
async fn gemini_follows_opaque_tokens_even_when_a_page_has_no_matching_models() {
    let token = "synthetic /token+?=&";
    let (result, requests) = fetch(
        Provider::Gemini,
        vec![
            (StatusCode::OK, json!({"models":[
                {"name":"models/embedding","supportedGenerationMethods":["embedContent"]},
                {"name":"models/live","supportedGenerationMethods":["bidiGenerateContent"]},
                {"name":"models/gemini-2.5-flash-image","displayName":"Nano Banana","supportedGenerationMethods":["generateContent"]},
                {"name":"models/gemini-2.5-flash-preview-tts","supportedGenerationMethods":["generateContent"]}
            ],"nextPageToken":token})),
            (StatusCode::OK, json!({"nextPageToken":"synthetic-next"})),
            (StatusCode::OK, json!({"models":[
                {"name":"models/gemini-chat","displayName":"Gemini Chat","supportedGenerationMethods":["countTokens","generateContent"]},
                {"name":"models/gemini-fallback","supportedGenerationMethods":["generateContent"]},
                {"name":"invalid-resource","supportedGenerationMethods":["generateContent"]},
                {"name":"models/no-methods"}
            ],"nextPageToken":""})),
        ],
    ).await;
    assert_eq!(
        result.unwrap()["models"],
        json!([
            {"id":"gemini-chat","label":"Gemini Chat"},
            {"id":"gemini-fallback","label":"gemini-fallback"}
        ])
    );
    assert_eq!(requests.len(), 3);
    for (request, cursor) in requests
        .iter()
        .zip([None, Some(token), Some("synthetic-next")])
    {
        assert_request(
            request,
            "/v1beta/models",
            "x-goog-api-key",
            "synthetic-credential",
        );
        let params = query(request);
        assert_eq!(params.get("pageSize").map(String::as_str), Some("1000"));
        assert_eq!(params.get("pageToken").map(String::as_str), cursor);
        assert_eq!(params.len(), if cursor.is_some() { 2 } else { 1 });
    }
}

#[tokio::test]
async fn empty_catalogs_are_successful_including_an_omitted_gemini_array() {
    for (provider, body) in [
        (Provider::Openai, json!({"data":[]})),
        (Provider::Openai, json!({"data":[{"id":"gpt-image-1"}]})),
        (
            Provider::Anthropic,
            json!({"data":[],"has_more":false,"first_id":null,"last_id":null}),
        ),
        (
            Provider::Anthropic,
            json!({"data":[{"id":"synthetic-embedding"}],"has_more":false}),
        ),
        (Provider::Gemini, json!({})),
        (Provider::Gemini, json!({"models":[]})),
        (
            Provider::Gemini,
            json!({"models":[{"name":"models/nano-banana-pro-preview","supportedGenerationMethods":["generateContent"]}]}),
        ),
    ] {
        let (result, requests) = fetch(provider, vec![(StatusCode::OK, body)]).await;
        let result = result.unwrap();
        assert_eq!(result["ok"], true);
        assert_eq!(result["models"], json!([]));
        assert_eq!(requests.len(), 1);
    }
}

fn assert_text_catalog(provider: Provider, included: &[&str], excluded: &[&str]) {
    let models = included
        .iter()
        .chain(excluded)
        .map(|id| match provider {
            Provider::Gemini => json!({
                "name":format!("models/{id}"),
                "supportedGenerationMethods":["generateContent", "countTokens"]
            }),
            _ => json!({"id":id}),
        })
        .collect::<Vec<_>>();
    let body = match provider {
        Provider::Openai => json!({"data":models}),
        Provider::Gemini => json!({"models":models}),
        Provider::Anthropic => json!({"data":models,"has_more":false}),
    };
    let page = parse_model_page(provider, &serde_json::to_vec(&body).unwrap()).unwrap();
    let actual = page
        .models
        .iter()
        .map(|(id, _)| id.as_str())
        .collect::<Vec<_>>();
    assert_eq!(actual, included);
}

#[test]
fn gemini_excludes_specialized_models_even_when_they_support_generate_content() {
    assert_text_catalog(
        Provider::Gemini,
        &[
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-3.1-flash-lite",
            "gemini-3.1-pro-preview",
            "gemini-flash-latest",
            "gemini-flash-lite-latest",
            "gemini-2.0-flash-thinking-exp-01-21",
            "gemma-3-27b-it",
            "gemma-4-26b-a4b-it",
        ],
        &[
            "gemini-2.5-flash-image",
            "gemini-2.0-flash-preview-image-generation",
            "gemini-3-pro-image-preview",
            "gemini-3.1-flash-image",
            "gemini-3.1-flash-lite-image",
            "nano-banana-pro-preview",
            "nanobanana",
            "imagen-4.0-generate-001",
            "veo-3.0-generate-001",
            "lyria-realtime-exp",
            "gemini-2.5-flash-preview-tts",
            "gemini-2.5-pro-preview-tts",
            "gemini-2.5-flash-native-audio-preview-12-2025",
            "gemini-2.0-flash-live-001",
            "gemini-live-2.5-flash-preview",
            "gemini-embedding-001",
            "embedding-001",
            "text-embedding-004",
            "aqa",
            "synthetic-unknown",
        ],
    );
}

#[test]
fn openai_keeps_language_models_and_classifies_fine_tunes_by_the_base_model() {
    assert_text_catalog(
        Provider::Openai,
        &[
            "gpt-4o",
            "gpt-4.1-mini",
            "gpt-5.4-mini",
            "gpt-6.1-sol",
            "gpt-5.3-codex",
            "gpt-5.4-pro",
            "gpt-4.1-2025-04-14",
            "chatgpt-4o-latest",
            "chat-latest",
            "o1",
            "o3-mini",
            "o4-mini-2025-04-16",
            "codex-mini-latest",
            "ft:gpt-4.1:synthetic:audio-image:id",
            "ft:o4-mini-2025-04-16:synthetic:custom:id",
        ],
        &[
            "gpt-image-1",
            "gpt-image-1-mini",
            "chatgpt-image-latest",
            "dall-e-3",
            "sora-2",
            "text-embedding-3-large",
            "omni-moderation-latest",
            "text-moderation-latest",
            "whisper-1",
            "tts-1-hd",
            "gpt-4o-mini-tts",
            "gpt-4o-transcribe",
            "gpt-4o-transcribe-diarize",
            "gpt-4o-audio-preview-2024-12-17",
            "gpt-audio",
            "gpt-realtime",
            "gpt-live-1",
            "gpt-4o-realtime-preview",
            "gpt-3.5-turbo-instruct",
            "babbage-002",
            "davinci-002",
            "ft:davinci-002:synthetic:gpt-4.1:id",
            "ft:whisper-1:synthetic:custom:id",
            "ft:gpt-4o-mini-tts:synthetic:custom:id",
            "ft:synthetic-unknown",
            "synthetic-unknown",
        ],
    );
}

#[test]
fn anthropic_keeps_claude_models_without_requiring_optional_capabilities() {
    assert_text_catalog(
        Provider::Anthropic,
        &[
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6",
            "claude-opus-4-6",
            "claude-opus-5",
        ],
        &[
            "synthetic-embedding",
            "synthetic-image",
            "synthetic-unknown",
        ],
    );
}

#[test]
fn gemini_keeps_valid_unicode_display_names() {
    // The upstream displayName limit is 128 UTF-8 characters, not 128 bytes.
    let label = "あ".repeat(128);
    let body = serde_json::to_vec(&json!({"models":[{
        "name":"models/gemini-unicode","displayName":label,
        "supportedGenerationMethods":["generateContent"]
    }]}))
    .unwrap();
    let page = parse_model_page(Provider::Gemini, &body).unwrap();
    assert_eq!(
        normalize_models(page.models),
        vec![json!({"id":"gemini-unicode","label":label})]
    );
}

#[test]
fn malformed_models_and_pagination_are_rejected() {
    for (provider, body) in [
        (Provider::Openai, json!({"models":[]})),
        (Provider::Openai, json!({"data":[{"id":42}]})),
        (Provider::Anthropic, json!({"data":[]})),
        (Provider::Anthropic, json!({"data":[],"has_more":"true"})),
        (Provider::Anthropic, json!({"data":[],"has_more":true})),
        (
            Provider::Anthropic,
            json!({"data":[],"has_more":true,"last_id":null}),
        ),
        (
            Provider::Anthropic,
            json!({"data":[],"has_more":true,"last_id":""}),
        ),
        (Provider::Gemini, json!({"models":{}})),
        (Provider::Gemini, json!({"models":[],"nextPageToken":123})),
        (Provider::Gemini, json!({"models":[{"name":42}]})),
        (
            Provider::Gemini,
            json!({"models":[{"name":"models/chat","supportedGenerationMethods":"generateContent"}]}),
        ),
    ] {
        let result = parse_model_page(provider, &serde_json::to_vec(&body).unwrap());
        assert!(matches!(
            result,
            Err(Error::Ai(super::super::AiError::Provider))
        ));
    }
}

#[tokio::test]
async fn later_page_http_errors_discard_partial_models_and_provider_details() {
    for status in [
        StatusCode::UNAUTHORIZED,
        StatusCode::FORBIDDEN,
        StatusCode::TOO_MANY_REQUESTS,
        StatusCode::INTERNAL_SERVER_ERROR,
    ] {
        let (result, requests) = fetch(Provider::Anthropic, vec![
            (StatusCode::OK, json!({"data":[{"id":"claude-a","display_name":"Claude A"}],"has_more":true,"last_id":"claude-a"})),
            (status, json!({"error":{"message":"synthetic-provider-detail synthetic-credential"}})),
        ]).await;
        let result = result.unwrap();
        assert_eq!(result["ok"], false);
        assert_eq!(result["models"], json!([]));
        assert_eq!(
            result["message"],
            if matches!(status.as_u16(), 401 | 403) {
                "APIキーを確認してください。"
            } else {
                "モデル一覧を取得できませんでした。"
            }
        );
        assert!(!result.to_string().contains("synthetic-"));
        assert_eq!(requests.len(), 2);
    }
}

#[tokio::test]
async fn cursor_cycles_are_rejected_for_both_paginated_providers() {
    for provider in [Provider::Anthropic, Provider::Gemini] {
        let responses = ["cursor-a", "cursor-b", "cursor-a"]
            .into_iter()
            .map(|cursor| {
                (
                    StatusCode::OK,
                    match provider {
                        Provider::Anthropic => json!({"data":[],"has_more":true,"last_id":cursor}),
                        _ => json!({"models":[],"nextPageToken":cursor}),
                    },
                )
            })
            .collect();
        let (result, requests) = fetch(provider, responses).await;
        assert!(matches!(
            result,
            Err(Error::Ai(super::super::AiError::Provider))
        ));
        assert_eq!(requests.len(), 3);
    }
}

#[tokio::test]
async fn endless_distinct_cursors_are_bounded() {
    let responses = (0..MAX_MODEL_PAGES)
        .map(|page| {
            (
                StatusCode::OK,
                json!({"nextPageToken":format!("cursor-{page}")}),
            )
        })
        .collect();
    let (result, requests) = fetch(Provider::Gemini, responses).await;
    assert!(matches!(
        result,
        Err(Error::Ai(super::super::AiError::Provider))
    ));
    assert_eq!(requests.len(), MAX_MODEL_PAGES);
}

#[tokio::test]
async fn model_and_byte_limits_apply_across_pages() {
    let first = json!({"models":(0..1000).map(|id| json!({
        "name":format!("models/gemini-{id}"),"supportedGenerationMethods":["generateContent"]
    })).collect::<Vec<_>>(),"nextPageToken":"cursor-a"});
    let mut second = first.clone();
    second["nextPageToken"] = json!("cursor-b");
    let third = json!({"models":[{"name":"models/gemini-last","supportedGenerationMethods":["generateContent"]}]});
    let (result, requests) = fetch(
        Provider::Gemini,
        vec![
            (StatusCode::OK, first),
            (StatusCode::OK, second),
            (StatusCode::OK, third),
        ],
    )
    .await;
    assert!(matches!(
        result,
        Err(Error::Ai(super::super::AiError::Provider))
    ));
    assert_eq!(requests.len(), 3);

    let large_page = json!({"models":[],"description":"x".repeat(MAX_MODEL_LIST_BYTES / 2),"nextPageToken":"cursor-a"});
    let (result, requests) = fetch(
        Provider::Gemini,
        vec![
            (StatusCode::OK, large_page.clone()),
            (StatusCode::OK, large_page),
        ],
    )
    .await;
    assert!(matches!(
        result,
        Err(Error::Ai(super::super::AiError::Provider))
    ));
    assert_eq!(requests.len(), 2);
}
