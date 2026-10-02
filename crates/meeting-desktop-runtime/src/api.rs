use crate::{
    runtime::{Runtime, Shared},
    wire, Config, Error,
};
mod agents;
use crate::dto::http as dto;
use futures_util::{SinkExt, StreamExt};
use poem::{
    endpoint::BoxEndpoint,
    http::{header, Method, StatusCode},
    listener::TcpAcceptor,
    middleware::Cors,
    web::{
        websocket::{Message, WebSocket, WebSocketStream},
        Data,
    },
    Endpoint, EndpointExt, IntoResponse, Request, Response, Route,
};
use poem_openapi::{payload::Json, OpenApiService};
mod http;
mod operations;
use meeting_storage::models as db;
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use tokio::sync::{watch, Mutex};

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
            let result = poem::Server::new_with_acceptor(TcpAcceptor::from_tokio(listener)?)
                .run_with_graceful_shutdown(
                    router,
                    async move {
                        let _ = stopping.changed().await;
                    },
                    None,
                )
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
        if result.is_ok() {
            // Release explicitly after requests and producers finish; external
            // process transports may defer dropping inherited descriptors.
            self.runtime.lock().await.release_ownership()?;
        }
        result
    }
}

const ORIGINS: [&str; 5] = [
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
];

/// Generate the contract without opening storage, credentials, workers or a socket.
pub fn openapi() -> String {
    OpenApiService::new(
        http::HttpApi,
        "Meeting Supporter",
        env!("CARGO_PKG_VERSION"),
    )
    .spec()
}

fn router(api: Api) -> BoxEndpoint<'static, Response> {
    let gate = api.clone();
    Route::new()
        .at("/ws", poem::get(websocket))
        .nest(
            "/",
            OpenApiService::new(
                http::HttpApi,
                "Meeting Supporter",
                env!("CARGO_PKG_VERSION"),
            ),
        )
        .data(api)
        .around(move |ep, mut req| {
            let api = gate.clone();
            async move {
                if let Err(status) = authorize(&api, &req) {
                    return Ok(
                        ApiError(status, "このリクエストを受け付けられません。".into())
                            .into_response(),
                    );
                }
                if req.uri().path() != "/ws" {
                    // Bound actual bytes, including chunked bodies without Content-Length.
                    let body = match req.take_body().into_bytes_limit(1024 * 1024).await {
                        Ok(body) => body,
                        Err(error) => {
                            let status =
                                if matches!(error, poem::error::ReadBodyError::PayloadTooLarge) {
                                    StatusCode::PAYLOAD_TOO_LARGE
                                } else {
                                    StatusCode::BAD_REQUEST
                                };
                            return Ok(ApiError(
                                status,
                                "リクエスト本文を受け付けられません。".into(),
                            )
                            .into_response());
                        }
                    };
                    req.set_body(body);
                }
                match ep.call(req).await {
                    Ok(response) => Ok(response),
                    Err(error) => {
                        if error.is_from_response() {
                            return Ok(error.into_response());
                        }
                        // Never expose framework parser errors containing submitted secrets.
                        let status = error.status();
                        let status = if error
                            .downcast_ref::<poem_openapi::error::ParseRequestPayloadError>()
                            .is_some()
                        {
                            StatusCode::UNPROCESSABLE_ENTITY
                        } else {
                            status
                        };
                        Ok(Json(dto::ErrorResponse {
                            detail: "リクエストを処理できませんでした。".into(),
                        })
                        .with_status(status)
                        .into_response())
                    }
                }
            }
        })
        .with(
            Cors::new()
                .allow_origins(ORIGINS)
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
        .boxed()
}
fn authorize(api: &Api, request: &Request) -> Result<(), StatusCode> {
    if *api.stopping.borrow() {
        return Err(StatusCode::SERVICE_UNAVAILABLE);
    }
    let headers = request.headers();
    if let Some(origin) = headers.get(header::ORIGIN) {
        if !origin
            .to_str()
            .is_ok_and(|origin| ORIGINS.contains(&origin))
        {
            return Err(StatusCode::FORBIDDEN);
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
    if authorized {
        Ok(())
    } else {
        Err(StatusCode::UNAUTHORIZED)
    }
}
#[poem::handler]
fn websocket(Data(api): Data<&Api>, ws: WebSocket) -> impl IntoResponse {
    let config = poem::web::websocket::WebSocketConfig::default()
        .max_message_size(Some(crate::references::MAX_MESSAGE))
        .max_frame_size(Some(crate::references::MAX_MESSAGE));
    let api = api.clone();
    ws.protocols([format!("auth.{}", api.token)])
        .config(config)
        .on_upgrade(move |socket| connection(api, socket))
}
async fn send(socket: &mut WebSocketStream, event: &wire::Event) -> bool {
    let Ok(text) = serde_json::to_string(event) else {
        return false;
    };
    matches!(
        tokio::time::timeout(
            std::time::Duration::from_secs(5),
            socket.send(Message::Text(text))
        )
        .await,
        Ok(Ok(()))
    )
}
async fn connection(mut api: Api, mut socket: WebSocketStream) {
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
            message = socket.next() => match message {
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
struct ApiError(StatusCode, dto::ErrorDetail);
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
        Json(dto::ErrorResponse { detail: self.1 })
            .with_status(self.0)
            .into_response()
    }
}

impl From<ApiError> for poem::Error {
    fn from(error: ApiError) -> Self {
        poem::Error::from_response(error.into_response())
    }
}
impl poem_openapi::ApiResponse for ApiError {
    fn meta() -> poem_openapi::registry::MetaResponses {
        poem_openapi::registry::MetaResponses {
            responses: [400, 401, 403, 404, 409, 413, 422, 500, 503]
                .into_iter()
                .map(|status| {
                    let mut response = Json::<dto::ErrorResponse>::meta().responses.remove(0);
                    response.status = Some(status);
                    response.description = "Request or runtime error";
                    response
                })
                .collect(),
        }
    }
    fn register(registry: &mut poem_openapi::registry::Registry) {
        Json::<dto::ErrorResponse>::register(registry);
    }
}
fn decode<T: serde::de::DeserializeOwned>(value: Value) -> Result<T, ApiError> {
    serde_json::from_value(value).map_err(|error| Error::from(error).into())
}
fn request<T: serde::de::DeserializeOwned>(body: impl serde::Serialize) -> Result<T, ApiError> {
    let value = serde_json::to_value(body).map_err(Error::from)?;
    serde_json::from_value(value).map_err(|_| {
        ApiError(
            StatusCode::UNPROCESSABLE_ENTITY,
            "リクエストの形式が不正です。".into(),
        )
    })
}
fn response<T: serde::de::DeserializeOwned>(value: Value) -> Result<Json<T>, ApiError> {
    decode(value).map(Json)
}
