//! Persistent ACP transports. Sessions and output belong to one request at a time.
use super::{registry::Launch, AgentError};
use agent_client_protocol::schema::{v1 as acp, ProtocolVersion};
use agent_client_protocol::{AcpAgent, AcpAgentConfig, Agent, Client, ConnectionTo};
use serde::Serialize;
use std::{
    collections::HashMap,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::{
    sync::{mpsc, oneshot, Mutex},
    task::JoinHandle,
};
use tracing::instrument::WithSubscriber;

#[derive(Clone, Serialize, Default)]
pub struct Status {
    pub ready: bool,
    pub auth_methods: Vec<AuthMethod>,
    pub message: String,
}
#[derive(Clone, Serialize)]
pub struct AuthMethod {
    pub id: String,
    pub name: String,
}
pub enum Chunk {
    Text(String),
    Done,
}
type Sink =
    Arc<std::sync::Mutex<Option<(acp::SessionId, mpsc::Sender<Result<Chunk, AgentError>>)>>>;
struct Connection {
    peer: ConnectionTo<Agent>,
    task: TransportTask,
    sink: Sink,
    overflow: Arc<AtomicBool>,
    auth_methods: Vec<AuthMethod>,
    close_supported: bool,
    idle_session: Option<acp::SessionId>,
    completed: usize,
}
struct TransportTask(JoinHandle<()>);
impl Drop for TransportTask {
    fn drop(&mut self) {
        self.0.abort();
    }
}
impl TransportTask {
    fn is_finished(&self) -> bool {
        self.0.is_finished()
    }
}
impl Connection {
    async fn open(launch: Launch) -> Result<Self, AgentError> {
        let sink: Sink = Arc::new(std::sync::Mutex::new(None));
        let updates = sink.clone();
        let permissions = sink.clone();
        let overflow = Arc::new(AtomicBool::new(false));
        let overflow_flag = overflow.clone();
        let (tx, rx) = oneshot::channel();
        let config = AcpAgentConfig::new(launch.command)
            .args(launch.args)
            .envs(launch.env);
        let task = TransportTask(tokio::spawn(
            async move {
                // Disable SDK protocol tracing even if the host enables verbose diagnostics.
                // Do not log SDK errors: they can contain raw stderr and protocol payloads.
                let _ = Client
                    .builder()
                    .name("meeting-supporter")
                    .on_receive_notification(
                        async move |notification: acp::SessionNotification, _cx| {
                            let guard = updates.lock().map_err(|_| acp::Error::internal_error())?;
                            if let Some((_, sender)) = guard
                                .as_ref()
                                .filter(|(id, _)| id == &notification.session_id)
                            {
                                let item = match notification.update {
                                    acp::SessionUpdate::AgentMessageChunk(chunk) => {
                                        match chunk.content {
                                            acp::ContentBlock::Text(text) => {
                                                Some(Ok(Chunk::Text(text.text)))
                                            }
                                            _ => None,
                                        }
                                    }
                                    acp::SessionUpdate::ToolCall(_) => Some(Err(AgentError::Tools)),
                                    _ => None,
                                };
                                if let Some(item) = item {
                                    if sender.try_send(item).is_err() {
                                        overflow_flag.store(true, Ordering::Release);
                                    }
                                }
                            }
                            Ok(())
                        },
                        agent_client_protocol::on_receive_notification!(),
                    )
                    .on_receive_request(
                        async move |request: acp::RequestPermissionRequest, responder, _cx| {
                            if let Ok(guard) = permissions.lock() {
                                if let Some((_, sender)) =
                                    guard.as_ref().filter(|(id, _)| id == &request.session_id)
                                {
                                    let _ = sender.try_send(Err(AgentError::Tools));
                                }
                            }
                            responder.respond(acp::RequestPermissionResponse::new(
                                acp::RequestPermissionOutcome::Cancelled,
                            ))
                        },
                        agent_client_protocol::on_receive_request!(),
                    )
                    .connect_with(
                        AcpAgent::new(config),
                        async move |peer: ConnectionTo<Agent>| {
                            let _ = tx.send(peer.clone());
                            peer.incoming_closed().await;
                            Ok(())
                        },
                    )
                    .await;
            }
            .with_subscriber(tracing::subscriber::NoSubscriber::default()),
        ));
        let peer = match tokio::time::timeout(Duration::from_secs(60), rx).await {
            Ok(Ok(peer)) => peer,
            _ => {
                return Err(AgentError::Connect);
            }
        };
        // Construct the owner before awaiting so cancellation always reaps the transport.
        let mut connection = Self {
            peer,
            task,
            sink,
            overflow,
            auth_methods: vec![],
            close_supported: false,
            idle_session: None,
            completed: 0,
        };
        let initialized = tokio::time::timeout(
            Duration::from_secs(60),
            connection
                .peer
                .send_request(
                    acp::InitializeRequest::new(ProtocolVersion::V1)
                        .client_info(acp::Implementation::new(
                            "meeting-supporter",
                            env!("CARGO_PKG_VERSION"),
                        ))
                        .client_capabilities(acp::ClientCapabilities::default()),
                )
                .block_task(),
        )
        .await
        .map_err(|_| AgentError::Timeout)?
        .map_err(map_error)?;
        if initialized.protocol_version != ProtocolVersion::V1 {
            return Err(AgentError::Protocol);
        }
        connection.auth_methods = initialized
            .auth_methods
            .into_iter()
            .filter_map(|method| {
                if let acp::AuthMethod::Agent(method) = method {
                    Some(AuthMethod {
                        id: method.id.to_string(),
                        name: method.name.chars().take(120).collect(),
                    })
                } else {
                    None
                }
            })
            .collect();
        connection.close_supported = initialized
            .agent_capabilities
            .session_capabilities
            .close
            .is_some();
        Ok(connection)
    }
    async fn session(&mut self, cwd: PathBuf) -> Result<acp::SessionId, AgentError> {
        if let Some(id) = self.idle_session.take() {
            return Ok(id);
        }
        let response = tokio::time::timeout(
            Duration::from_secs(30),
            self.peer
                .send_request(acp::NewSessionRequest::new(cwd))
                .block_task(),
        )
        .await
        .map_err(|_| AgentError::Timeout)?
        .map_err(map_error)?;
        Ok(response.session_id)
    }
    async fn close_session(&self, id: acp::SessionId) {
        if self.close_supported {
            let _ = tokio::time::timeout(
                Duration::from_secs(2),
                self.peer
                    .send_request(acp::CloseSessionRequest::new(id))
                    .block_task(),
            )
            .await;
        }
    }
}
fn map_error(error: acp::Error) -> AgentError {
    if error.code == acp::ErrorCode::AuthRequired {
        AgentError::Auth
    } else {
        AgentError::Connect
    }
}
#[derive(Default)]
struct Slot {
    connection: Mutex<Option<Connection>>,
    status: std::sync::Mutex<Status>,
    cancel: tokio::sync::Notify,
}
#[derive(Default)]
pub struct Pool {
    slots: Mutex<HashMap<String, Arc<Slot>>>,
}
impl Pool {
    async fn slot(&self, id: &str) -> Arc<Slot> {
        self.slots
            .lock()
            .await
            .entry(id.into())
            .or_default()
            .clone()
    }
    pub async fn status(&self, id: &str) -> Status {
        let slot = self.slot(id).await;
        let mut status = slot.status.lock().unwrap().clone();
        if let Ok(connection) = slot.connection.try_lock() {
            if connection.as_ref().is_none_or(|c| c.task.is_finished()) {
                status.ready = false;
            }
        }
        status
    }
    pub async fn connect(
        &self,
        launch: Launch,
        cwd: PathBuf,
        method: Option<String>,
    ) -> Result<Status, AgentError> {
        let slot = self.slot(&launch.id).await;
        let mut guard = slot.connection.try_lock().map_err(|_| AgentError::Busy)?;
        if guard.as_ref().is_none_or(|c| c.task.is_finished()) {
            let opened = tokio::select! { result = Connection::open(launch) => result, _ = slot.cancel.notified() => Err(AgentError::Cancelled) };
            match opened {
                Ok(connection) => *guard = Some(connection),
                Err(error) => {
                    *slot.status.lock().unwrap() = Status {
                        message: error.to_string(),
                        ..Status::default()
                    };
                    return Err(error);
                }
            }
        }
        let connection = guard.as_mut().ok_or(AgentError::Connect)?;
        let operation = async {
            if let Some(method) = method {
                if !connection.auth_methods.iter().any(|m| m.id == method) {
                    return Err(AgentError::Auth);
                }
                // 認証前のセッションを使うと、変更後の認証状態を確認できない。
                if let Some(id) = connection.idle_session.take() {
                    connection.close_session(id).await;
                }
                tokio::time::timeout(
                    Duration::from_secs(180),
                    connection
                        .peer
                        .send_request(acp::AuthenticateRequest::new(method))
                        .block_task(),
                )
                .await
                .map_err(|_| AgentError::Timeout)?
                .map_err(map_error)?;
            }
            let id = connection.session(cwd).await?;
            connection.idle_session = Some(id);
            Ok::<_, AgentError>(())
        };
        let result = tokio::select! { result = operation => result, _ = slot.cancel.notified() => Err(AgentError::Cancelled) };
        let status = Status {
            ready: result.is_ok(),
            auth_methods: connection.auth_methods.clone(),
            message: result
                .as_ref()
                .map(|_| "接続済みです。".into())
                .unwrap_or_else(|e| e.to_string()),
        };
        *slot.status.lock().unwrap() = status.clone();
        if matches!(
            result,
            Err(AgentError::Timeout | AgentError::Cancelled | AgentError::Connect)
        ) {
            *guard = None;
        }
        // Auth-required is a useful setup state, not an HTTP failure.
        match result {
            Ok(()) | Err(AgentError::Auth) => Ok(status),
            Err(error) => Err(error),
        }
    }
    pub async fn stream(
        self: &Arc<Self>,
        launch: Launch,
        cwd: PathBuf,
        prompt: String,
    ) -> mpsc::Receiver<Result<Chunk, AgentError>> {
        let (tx, rx) = mpsc::channel(256);
        let pool = self.clone();
        tokio::spawn(async move {
            let slot = pool.slot(&launch.id).await;
            let mut guard = tokio::select! { guard = slot.connection.lock() => guard, _ = tx.closed() => return };
            let result = async {
                if guard
                    .as_ref()
                    .is_none_or(|c| c.task.is_finished() || c.completed >= 32)
                {
                    *guard = Some(Connection::open(launch).await?);
                }
                let connection = guard.as_mut().ok_or(AgentError::Connect)?;
                let session = connection.session(cwd).await?;
                connection.overflow.store(false, Ordering::Release);
                *connection.sink.lock().unwrap() = Some((session.clone(), tx.clone()));
                let request = connection
                    .peer
                    .send_request(acp::PromptRequest::new(
                        session.clone(),
                        vec![acp::ContentBlock::Text(acp::TextContent::new(prompt))],
                    ))
                    .block_task();
                tokio::pin!(request);
                let response = tokio::select! {
                    response = &mut request => response.map_err(map_error),
                    _ = tx.closed() => {
                        let _ = connection.peer.send_notification(acp::CancelNotification::new(session.clone()));
                        let _ = tokio::time::timeout(Duration::from_secs(1), &mut request).await;
                        Err(AgentError::Cancelled)
                    }
                    _ = tokio::time::sleep(Duration::from_secs(90)) => {
                        let _ = connection.peer.send_notification(acp::CancelNotification::new(session.clone()));
                        Err(AgentError::Timeout)
                    }
                };
                *connection.sink.lock().unwrap() = None;
                if response?.stop_reason != acp::StopReason::EndTurn
                    || connection.overflow.load(Ordering::Acquire)
                {
                    return Err(AgentError::Incomplete);
                }
                *slot.status.lock().unwrap() = Status {
                    ready: true,
                    auth_methods: connection.auth_methods.clone(),
                    message: "接続済みです。".into(),
                };
                connection.completed += 1;
                connection.close_session(session).await;
                Ok::<_, AgentError>(())
            };
            let result = tokio::select! { result = result => result, _ = tx.closed() => Err(AgentError::Cancelled), _ = slot.cancel.notified() => Err(AgentError::Cancelled) };
            // A cancelled/failed session never becomes the next request's transport.
            if let Err(error) = &result {
                if let Some(connection) = &*guard {
                    if let Some((session, _)) = connection.sink.lock().unwrap().take() {
                        let _ = connection
                            .peer
                            .send_notification(acp::CancelNotification::new(session));
                    }
                }
                *guard = None;
                *slot.status.lock().unwrap() = Status {
                    message: error.to_string(),
                    ..Status::default()
                };
            }
            let _ = tx.send(result.map(|_| Chunk::Done)).await;
        });
        rx
    }
    pub async fn disconnect(&self, id: &str) -> Result<(), AgentError> {
        let slot = self.slot(id).await;
        let mut guard = slot.connection.try_lock().map_err(|_| AgentError::Busy)?;
        *guard = None;
        *slot.status.lock().unwrap() = Status::default();
        Ok(())
    }
    pub async fn adopt(&self, id: &str, verified: Self) {
        if let Some(slot) = verified.slots.lock().await.remove(id) {
            // 呼出側が更新操作を直列化し、会議開始を止めている間に切り替える。
            let previous = self.slots.lock().await.insert(id.into(), slot);
            if let Some(previous) = previous {
                *previous.connection.lock().await = None;
            }
        }
    }
    pub async fn shutdown(&self) {
        for slot in self.slots.lock().await.values() {
            slot.cancel.notify_one();
            *slot.connection.lock().await = None;
        }
    }
}
