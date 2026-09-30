use crate::{
    runtime::{Runtime, Shared},
    wire, Config, Error,
};
mod agents;
use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        DefaultBodyLimit, Path, Query, State,
    },
    http::{header, HeaderMap, Method, Request, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use meeting_storage::models as db;
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use tokio::sync::{watch, Mutex};
use tower::ServiceExt;
use tower_http::{cors::CorsLayer, services::ServeFile};

#[derive(Clone)]
struct Api {
    shared: Arc<Shared>,
    runtime: Arc<Mutex<Runtime>>,
    token: String,
    cleanup_preview: Arc<Mutex<Option<crate::cleanup::Plan>>>,
    stopping: watch::Receiver<bool>,
}
pub struct Server {
    pub port: u16,
    pub token: String,
    runtime: Arc<Mutex<Runtime>>,
    stop: watch::Sender<bool>,
    task: tokio::task::JoinHandle<std::io::Result<()>>,
    alive: Arc<AtomicBool>,
    agent_updates: Option<tokio::task::JoinHandle<()>>,
}
impl Server {
    pub async fn start(config: Config) -> Result<Self, Error> {
        Self::start_with_secrets(config, Arc::new(crate::settings::UnavailableSecrets)).await
    }
    pub async fn start_with_secrets(
        config: Config,
        secrets: Arc<dyn crate::settings::Secrets>,
    ) -> Result<Self, Error> {
        let (stop, stopping) = watch::channel(false);
        let runtime = Runtime::open(config, stopping.clone(), secrets).await?;
        let shared = runtime.shared.clone();
        let runtime = Arc::new(Mutex::new(runtime));
        let token = format!(
            "{}{}",
            uuid::Uuid::new_v4().simple(),
            uuid::Uuid::new_v4().simple()
        );
        let api = Api {
            shared,
            runtime: runtime.clone(),
            token: token.clone(),
            cleanup_preview: Arc::new(Mutex::new(None)),
            stopping: stopping.clone(),
        };
        let router = router(api.clone());
        let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0)).await?;
        let port = listener.local_addr()?.port();
        let agent_updates = api
            .shared
            .config
            .agent_updates
            .then(|| tokio::spawn(agents::background(api)));
        let alive = Arc::new(AtomicBool::new(true));
        let running = alive.clone();
        let task = tokio::spawn(async move {
            let mut stopping = stopping;
            let result = axum::serve(listener, router)
                .with_graceful_shutdown(async move {
                    let _ = stopping.changed().await;
                })
                .await;
            running.store(false, Ordering::Release);
            result
        });
        Ok(Self {
            port,
            token,
            runtime,
            stop,
            task,
            alive,
            agent_updates,
        })
    }
    pub fn is_running(&self) -> bool {
        self.alive.load(Ordering::Acquire)
    }
    pub async fn shutdown(self) -> Result<(), Error> {
        let _ = self.stop.send(true);
        if let Some(task) = self.agent_updates {
            let _ = task.await;
        }
        let result = self.runtime.lock().await.shutdown().await;
        self.task.await.map_err(|_| Error::Closed)??;
        result
    }
}
fn router(api: Api) -> Router {
    let origins = [
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ];
    Router::new()
        .route(
            "/health",
            get(|| async { Json(json!({"status":"ok","runtime":"rust"})) }),
        )
        .route("/ws", get(websocket))
        .route("/api/settings", get(settings_get).post(settings_save))
        .route("/api/stt/capabilities", get(speech_capabilities))
        .route("/api/stt/model", get(model_status))
        .route(
            "/api/stt/model/download",
            axum::routing::post(model_download),
        )
        .route("/api/stt/model/cancel", axum::routing::post(model_cancel))
        .route("/meetings", get(list))
        .route(
            "/meetings/recordings/cleanup/preview",
            axum::routing::post(cleanup_preview),
        )
        .route(
            "/meetings/recordings/cleanup",
            axum::routing::post(cleanup_execute),
        )
        .route("/meetings/{id}", get(detail).patch(title).delete(delete))
        .route("/meetings/{id}/recordings", get(recordings))
        .route("/meetings/{id}/recordings/{role}", get(recording))
        .route("/api/ai/routes", get(ai_routes))
        .route("/api/ai/agents", get(agents::catalog))
        .route(
            "/api/ai/agents/update-all",
            axum::routing::post(agents::update_all),
        )
        .route(
            "/api/ai/agents/{id}/install",
            axum::routing::post(agents::install),
        )
        .route(
            "/api/ai/agents/{id}/connect",
            axum::routing::post(agents::connect),
        )
        .route("/api/ai/agents/{id}", axum::routing::delete(agents::remove))
        .route(
            "/api/ai/routes/assignments",
            axum::routing::put(ai_assignments),
        )
        .route("/api/settings/ollama/models", get(ollama_models))
        .route(
            "/api/settings/connections/test",
            axum::routing::post(connection_test),
        )
        .fallback(|| async {
            (
                StatusCode::NOT_IMPLEMENTED,
                Json(json!({"detail":"この機能は Rust バックエンドへの移植中です。"})),
            )
        })
        .layer(DefaultBodyLimit::max(1024 * 1024))
        .layer(middleware::from_fn_with_state(api.clone(), authorize))
        .layer(
            CorsLayer::new()
                .allow_origin(origins.map(|s| s.parse().unwrap()))
                .allow_headers([header::AUTHORIZATION, header::CONTENT_TYPE, header::RANGE])
                .allow_methods([
                    Method::GET,
                    Method::PATCH,
                    Method::DELETE,
                    Method::POST,
                    Method::PUT,
                ])
                .expose_headers([header::CONTENT_RANGE, header::ACCEPT_RANGES]),
        )
        .with_state(api)
}
async fn authorize(
    State(api): State<Api>,
    request: Request<axum::body::Body>,
    next: Next,
) -> Response {
    if *api.stopping.borrow() {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    }
    let headers = request.headers();
    if let Some(origin) = headers.get(header::ORIGIN) {
        if !matches!(
            origin.to_str(),
            Ok("http://localhost:1420"
                | "http://127.0.0.1:1420"
                | "tauri://localhost"
                | "http://tauri.localhost"
                | "https://tauri.localhost")
        ) {
            return StatusCode::FORBIDDEN.into_response();
        }
    }
    let authorized = if request.uri().path() == "/ws" {
        headers
            .get(header::SEC_WEBSOCKET_PROTOCOL)
            .and_then(|v| v.to_str().ok())
            .is_some_and(|v| {
                v.split(',')
                    .any(|v| v.trim() == format!("auth.{}", api.token))
            })
    } else {
        headers
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .is_some_and(|v| v == format!("Bearer {}", api.token))
    };
    if !authorized {
        return StatusCode::UNAUTHORIZED.into_response();
    }
    next.run(request).await
}
async fn websocket(State(api): State<Api>, ws: WebSocketUpgrade) -> Response {
    ws.protocols([format!("auth.{}", api.token)])
        .max_message_size(crate::references::MAX_MESSAGE)
        .max_frame_size(crate::references::MAX_MESSAGE)
        .on_upgrade(move |socket| connection(api, socket))
}
async fn send(socket: &mut WebSocket, event: &wire::Event) -> bool {
    let Ok(text) = serde_json::to_string(event) else {
        return false;
    };
    matches!(
        tokio::time::timeout(
            std::time::Duration::from_secs(5),
            socket.send(Message::Text(text.into()))
        )
        .await,
        Ok(Ok(()))
    )
}
async fn connection(mut api: Api, mut socket: WebSocket) {
    let (snapshot, mut events) = api.shared.snapshot().await;
    for event in snapshot {
        if !send(&mut socket, &event).await {
            return;
        }
    }
    // One bounded command worker per socket: preparing must not block event delivery.
    let (commands, mut receive) = tokio::sync::mpsc::channel(8);
    let worker_api = api.clone();
    let worker = tokio::spawn(async move {
        worker_api.runtime.lock().await.ensure_monitors().await;
        while let Some((command, revision)) = receive.recv().await {
            if *worker_api.stopping.borrow() {
                break;
            }
            if let Err(error) = worker_api
                .runtime
                .lock()
                .await
                .dispatch(command, revision)
                .await
            {
                worker_api.shared.error(&error);
            }
        }
    });
    loop {
        tokio::select! {
            _ = api.stopping.changed() => break,
            event = events.recv() => match event {
                Ok(event) => if !send(&mut socket, &event).await { break; },
                Err(_) => break, // A slow consumer reconnects for an authoritative snapshot.
            },
            message = socket.recv() => match message {
                Some(Ok(Message::Text(text))) => match serde_json::from_str::<wire::Command>(&text) {
                    Ok(command) => {
                        if matches!(command, wire::Command::ShutdownStt {}) { api.shared.cancel_prepare.send_modify(|v| *v = v.wrapping_add(1)); }
                        let revision = *api.shared.cancel_prepare.borrow();
                        if commands.try_send((command, revision)).is_err() && !send(&mut socket, &wire::Event::Error { text: "操作が多すぎます。処理完了を待ってください。".into() }).await { break; }
                    }
                    Err(_) => if !send(&mut socket, &wire::Event::Error { text: "未対応の操作、または不正なメッセージです。".into() }).await { break; },
                },
                Some(Ok(Message::Ping(bytes))) => if socket.send(Message::Pong(bytes)).await.is_err() { break; },
                Some(Ok(Message::Pong(_))) => {},
                _ => break,
            }
        }
    }
    drop(commands);
    // Accepted effects survive disconnects. Dropping an in-flight save is unsafe.
    let _ = worker.await;
}
struct ApiError(StatusCode, Value);
impl From<Error> for ApiError {
    fn from(error: Error) -> Self {
        let status = match &error {
            Error::CleanupPolicy => StatusCode::BAD_REQUEST,
            Error::CleanupChanged | Error::CleanupFiles => StatusCode::CONFLICT,
            Error::Ai(crate::ai::AiError::Configuration | crate::ai::AiError::Unsupported) => {
                StatusCode::UNPROCESSABLE_ENTITY
            }
            Error::Ai(crate::ai::AiError::Busy) => StatusCode::CONFLICT,
            Error::Busy | Error::Agent(crate::agents::AgentError::Busy) => StatusCode::CONFLICT,
            Error::Agent(_) => StatusCode::UNPROCESSABLE_ENTITY,
            _ => StatusCode::INTERNAL_SERVER_ERROR,
        };
        Self(status, error.to_string().into())
    }
}
impl From<meeting_storage::StorageError> for ApiError {
    fn from(error: meeting_storage::StorageError) -> Self {
        Error::from(error).into()
    }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"detail":self.1}))).into_response()
    }
}
type Reply = Result<Json<Value>, ApiError>;
#[derive(Deserialize)]
struct Page {
    #[serde(default = "default_limit")]
    limit: u32,
    #[serde(default)]
    offset: u32,
}
fn default_limit() -> u32 {
    50
}
async fn list(State(api): State<Api>, Query(page): Query<Page>) -> Reply {
    if !(1..=200).contains(&page.limit) {
        return Err(ApiError(StatusCode::BAD_REQUEST, "invalid limit".into()));
    }
    let mut items = api
        .shared
        .repository
        .execute(db::Command::ListMeetings {
            limit: page.limit,
            offset: page.offset,
        })
        .await?;
    if let Some(items) = items.as_array_mut() {
        for item in items {
            item["has_ai_note"] = json!(!item["ai_note"]
                .as_str()
                .unwrap_or_default()
                .trim()
                .is_empty());
        }
    }
    let total = api
        .shared
        .repository
        .execute(db::Command::CountMeetings {})
        .await?;
    Ok(Json(
        json!({"items":items,"total":total,"limit":page.limit,"offset":page.offset}),
    ))
}
async fn get_meeting(api: &Api, id: &str) -> Result<Value, ApiError> {
    let meeting = api
        .shared
        .repository
        .execute(db::Command::GetMeeting {
            meeting_id: id.into(),
        })
        .await?;
    if meeting.is_null() {
        return Err(ApiError(StatusCode::NOT_FOUND, "Meeting not found".into()));
    }
    Ok(meeting)
}
async fn detail(State(api): State<Api>, Path(id): Path<String>) -> Reply {
    let mut meeting = get_meeting(&api, &id).await?;
    meeting["turns"] = api
        .shared
        .repository
        .execute(db::Command::ListTurns {
            meeting_id: id.clone(),
        })
        .await?;
    meeting["reply_suggestions"] = api
        .shared
        .repository
        .execute(db::Command::ListReplySuggestions {
            meeting_id: id.clone(),
        })
        .await?;
    meeting["recording_assets"] = api
        .shared
        .repository
        .execute(db::Command::ListRecordingAssets { meeting_id: id })
        .await?;
    Ok(Json(meeting))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Title {
    title: String,
}
async fn title(State(api): State<Api>, Path(id): Path<String>, Json(body): Json<Title>) -> Reply {
    get_meeting(&api, &id).await?;
    api.shared
        .repository
        .execute(db::Command::UpdateMeetingTitle {
            meeting_id: id,
            title: body.title,
        })
        .await?;
    Ok(Json(json!({"ok":true})))
}
async fn delete(State(api): State<Api>, Path(id): Path<String>) -> Reply {
    // Accepted deletion finishes even if the HTTP client disconnects.
    tokio::spawn(async move {
        let _guard = api
            .runtime
            .try_lock()
            .map_err(|_| ApiError(StatusCode::CONFLICT, "会議を処理中です。".into()))?;
        let meeting = get_meeting(&api, &id).await?;
        if meeting["status"] == "active" {
            return Err(ApiError(
                StatusCode::CONFLICT,
                "未確定の会議は保持されます。".into(),
            ));
        }
        let root = api.shared.config.data_dir.clone();
        let owned_id = id.clone();
        tokio::task::spawn_blocking(move || crate::cleanup::remove_files(&root, &owned_id))
            .await
            .map_err(|_| Error::CleanupFiles)??;
        api.shared
            .repository
            .execute(db::Command::DeleteMeeting { meeting_id: id })
            .await?;
        Ok(Json(json!({"ok":true})))
    })
    .await
    .map_err(|_| Error::CleanupFiles)?
}
async fn cleanup_preview(
    State(api): State<Api>,
    Json(body): Json<crate::cleanup::Request>,
) -> Reply {
    let _guard = api
        .runtime
        .try_lock()
        .map_err(|_| ApiError(StatusCode::CONFLICT, "会議を処理中です。".into()))?;
    let plan = crate::cleanup::plan(&api.shared.repository, &body).await?;
    let response = serde_json::to_value(&plan.preview).map_err(Error::from)?;
    *api.cleanup_preview.lock().await = Some(plan);
    Ok(Json(response))
}
async fn cleanup_execute(
    State(api): State<Api>,
    Json(body): Json<crate::cleanup::Request>,
) -> Reply {
    tokio::spawn(async move {
        let _guard = api
            .runtime
            .try_lock()
            .map_err(|_| ApiError(StatusCode::CONFLICT, "会議を処理中です。".into()))?;
        let previous = api
            .cleanup_preview
            .lock()
            .await
            .take()
            .ok_or(Error::CleanupChanged)?;
        let result = crate::cleanup::execute(&api.shared, &body, previous).await?;
        Ok(Json(serde_json::to_value(result).map_err(Error::from)?))
    })
    .await
    .map_err(|_| Error::CleanupFiles)?
}

async fn recordings(State(api): State<Api>, Path(id): Path<String>) -> Reply {
    get_meeting(&api, &id).await?;
    Ok(Json(
        api.shared
            .repository
            .execute(db::Command::ListRecordingAssets { meeting_id: id })
            .await?,
    ))
}
async fn contained(
    base: &std::path::Path,
    path: &std::path::Path,
) -> Result<std::path::PathBuf, ApiError> {
    let base = tokio::fs::canonicalize(base).await.map_err(Error::from)?;
    let path = tokio::fs::canonicalize(path)
        .await
        .map_err(|_| ApiError(StatusCode::NOT_FOUND, "Recording not found".into()))?;
    if !path.starts_with(&base) || path == base {
        return Err(ApiError(StatusCode::BAD_REQUEST, "Invalid path".into()));
    }
    Ok(path)
}
async fn recording(
    State(api): State<Api>,
    Path((id, role)): Path<(String, db::Role)>,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    let asset = api
        .shared
        .repository
        .execute(db::Command::GetRecordingAssetByRole {
            meeting_id: id,
            role,
        })
        .await?;
    if asset.is_null() {
        return Err(ApiError(
            StatusCode::NOT_FOUND,
            "Recording not found".into(),
        ));
    }
    let asset: db::Asset = serde_json::from_value(asset).map_err(Error::from)?;
    let path = contained(
        &api.shared.config.data_dir,
        &api.shared.config.data_dir.join(asset.relative_path),
    )
    .await?;
    let mut request = Request::new(axum::body::Body::empty());
    *request.headers_mut() = headers;
    let response = ServeFile::new(path)
        .oneshot(request)
        .await
        .unwrap_or_else(|never| match never {});
    Ok(response.into_response())
}

async fn settings_value(api: &Api) -> Result<Value, ApiError> {
    let guard = api.shared.settings.lock().await;
    let store = guard.clone();
    let response = tokio::task::spawn_blocking(move || store.response())
        .await
        .map_err(|_| Error::Closed)?;
    drop(guard);
    let mut response = response?;
    let id = api
        .shared
        .live
        .lock()
        .await
        .session
        .as_ref()
        .map(|s| s.id.clone());
    if let Some(id) = id {
        let path = api.shared.config.data_dir.join("usage.jsonl");
        let summary = tokio::task::spawn_blocking(move || crate::usage::meeting(&path, &id))
            .await
            .map_err(|_| Error::Closed)??;
        response["usage"]["current_meeting"] =
            serde_json::to_value(summary).map_err(Error::from)?;
    }
    Ok(response)
}
async fn settings_get(State(api): State<Api>) -> Reply {
    Ok(Json(settings_value(&api).await?))
}
fn settings_error(error: Error) -> ApiError {
    match error {
        Error::Busy | Error::Session(_) => ApiError(
            StatusCode::CONFLICT,
            json!({"code":"AUDIO_SETTINGS_LOCKED","message":"会議中・準備中は音声認識の設定を変更できません。"}),
        ),
        Error::Settings | Error::Unsupported => {
            ApiError(StatusCode::UNPROCESSABLE_ENTITY, error.to_string().into())
        }
        _ => error.into(),
    }
}
async fn settings_save(State(api): State<Api>, Json(patch): Json<crate::settings::Patch>) -> Reply {
    let mut runtime = api
        .runtime
        .try_lock()
        .map_err(|_| settings_error(Error::Busy))?;
    runtime.save_settings(patch).await.map_err(settings_error)?;
    Ok(Json(
        json!({"ok":true,"settings":settings_value(&api).await?}),
    ))
}
async fn speech_capabilities(State(api): State<Api>) -> Json<Value> {
    let supported = meeting_media_runtime::whisper_gpu_supported(&api.shared.config.speech_worker)
        .await
        .ok();
    // null means unknown (missing, outdated or failing worker), never CPU-only.
    Json(json!({"whisper_gpu": supported}))
}

async fn model_status(
    State(api): State<Api>,
    Query(query): Query<crate::models::Request>,
) -> Reply {
    Ok(Json(
        serde_json::to_value(api.shared.models.status(&query).map_err(model_error)?)
            .map_err(Error::from)?,
    ))
}
async fn model_download(
    State(api): State<Api>,
    Json(query): Json<crate::models::Request>,
) -> Reply {
    Ok(Json(
        serde_json::to_value(api.shared.models.start(&query).map_err(model_error)?)
            .map_err(Error::from)?,
    ))
}
async fn model_cancel(
    State(api): State<Api>,
    Query(query): Query<crate::models::Request>,
) -> Reply {
    Ok(Json(
        serde_json::to_value(api.shared.models.cancel(&query).map_err(model_error)?)
            .map_err(Error::from)?,
    ))
}
fn model_error(error: crate::models::ModelError) -> ApiError {
    ApiError(
        StatusCode::UNPROCESSABLE_ENTITY,
        json!({"detail":error.to_string()}),
    )
}

async fn ai_routes(State(api): State<Api>) -> Reply {
    let store = api.shared.settings.lock().await.clone();
    // Warm the previously selected agent on startup without prompting the model.
    if let Some(id) = crate::ai::routes::assignments(&store)?
        .reply
        .and_then(|id| id.strip_prefix("acp:").map(str::to_owned))
    {
        if !api.shared.live.lock().await.running && !api.shared.agents.pool.status(&id).await.ready
        {
            if let Ok(_guard) = api.shared.agents.maintenance.try_lock() {
                let _ = api.shared.agents.connect(&id, None).await;
            }
        }
    }
    Ok(Json(
        crate::ai::routes::catalog(store, &api.shared.agents).await?,
    ))
}
async fn ai_assignments(
    State(api): State<Api>,
    Json(body): Json<crate::ai::routes::Assignments>,
) -> Reply {
    let _runtime = api.runtime.try_lock().map_err(|_| {
        ApiError(
            StatusCode::CONFLICT,
            "会議の操作中です。完了を待ってください。".into(),
        )
    })?;
    let mut guard = api.shared.settings.lock().await;
    let old = crate::ai::routes::assignments(&guard)?;
    // Keep existing assignments for not-yet-ported use cases, never silently replace them.
    if body.minutes != old.minutes {
        return Err(ApiError(
            StatusCode::UNPROCESSABLE_ENTITY,
            "議事録の割当変更は移植中です。".into(),
        ));
    }
    if let Some(id) = &body.reply {
        if let Some(agent) = id.strip_prefix("acp:") {
            api.shared.agents.launch(agent).await.map_err(Error::from)?;
            if !api.shared.agents.pool.status(agent).await.ready {
                return Err(Error::Agent(crate::agents::AgentError::Connect).into());
            }
        } else if !matches!(id.as_str(), "openai" | "gemini" | "anthropic" | "ollama") {
            return Err(ApiError(
                StatusCode::UNPROCESSABLE_ENTITY,
                "このAI経路はまだ選択できません。".into(),
            ));
        }
    }
    let mut candidate = guard.document.clone();
    let mut values = serde_json::to_value(&body)
        .map_err(Error::from)?
        .as_object()
        .cloned()
        .ok_or(Error::Settings)?;
    values.retain(|_, v| !v.is_null());
    candidate["ai"]["assignments"] = Value::Object(values);
    let mut store = guard.clone();
    let selected = body.reply;
    let saved = tokio::task::spawn_blocking(move || {
        if let Some(id) = selected.filter(|id| !id.starts_with("acp:")) {
            if let Err(Error::Ai(crate::ai::AiError::Unsupported)) =
                crate::ai::routes::resolve(&store, &id)
            {
                return Err(crate::ai::AiError::Unsupported.into());
            }
        }
        store.save(candidate, serde_json::from_value(json!({}))?)?;
        Ok::<_, Error>(store)
    })
    .await
    .map_err(|_| Error::Closed)??;
    *guard = saved.clone();
    drop(guard);
    api.shared
        .replies
        .lock()
        .await
        .cancel_all(&api.shared)
        .await;
    Ok(Json(
        crate::ai::routes::catalog(saved, &api.shared.agents).await?,
    ))
}
#[derive(Deserialize)]
struct OllamaQuery {
    base_url: Option<String>,
}
async fn ollama_models(State(api): State<Api>, Query(query): Query<OllamaQuery>) -> Reply {
    let base_url = match query.base_url {
        Some(url) => url,
        None => api.shared.settings.lock().await.document["ollama"]["base_url"]
            .as_str()
            .ok_or(Error::Settings)?
            .into(),
    };
    match crate::ai::routes::ollama_models(&base_url).await {
        Ok(models) => Ok(Json(
            json!({"ok":true,"base_url":base_url,"models":models,"message":null}),
        )),
        Err(error) => Ok(Json(
            json!({"ok":false,"base_url":base_url,"models":[],"message":error.to_string()}),
        )),
    }
}

async fn connection_test(
    State(api): State<Api>,
    Json(body): Json<crate::ai::connections::Request>,
) -> Reply {
    let store = api.shared.settings.lock().await.clone();
    Ok(Json(crate::ai::connections::check(store, body).await?))
}
