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
async fn openai_uses_a_single_bearer_authenticated_list_without_name_filters() {
    let (result, requests) = fetch(
        Provider::Openai,
        vec![(
            StatusCode::OK,
            json!({"object":"list","data":[
                {"id":"synthetic-z","object":"model","created":123,"owned_by":"openai","shutdown_date":null},
                {"id":"ft:synthetic","object":"model","created":123,"owned_by":"organization"},
                {"id":"synthetic-a","object":"model","created":123,"owned_by":"openai"}
            ]}),
        )],
    )
    .await;
    assert_eq!(
        result.unwrap(),
        json!({"ok":true,"provider":"openai","message":null,"models":[
            {"id":"ft:synthetic","label":"ft:synthetic"},
            {"id":"synthetic-a","label":"synthetic-a"},
            {"id":"synthetic-z","label":"synthetic-z"}
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
                {"id":"claude-shared","display_name":"Charlie"}
            ],"has_more":true,"first_id":"claude-z","last_id":"claude-shared"}),
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
    for (request, cursor) in requests
        .iter()
        .zip([None, Some("claude-shared"), Some("claude-b")])
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
                {"name":"models/live","supportedGenerationMethods":["bidiGenerateContent"]}
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
        (
            Provider::Anthropic,
            json!({"data":[],"has_more":false,"first_id":null,"last_id":null}),
        ),
        (Provider::Gemini, json!({})),
        (Provider::Gemini, json!({"models":[]})),
    ] {
        let (result, requests) = fetch(provider, vec![(StatusCode::OK, body)]).await;
        let result = result.unwrap();
        assert_eq!(result["ok"], true);
        assert_eq!(result["models"], json!([]));
        assert_eq!(requests.len(), 1);
    }
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
