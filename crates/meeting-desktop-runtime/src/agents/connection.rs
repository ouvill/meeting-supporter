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
    pub model: Option<ModelSelector>,
    pub thought_level: Option<ModelSelector>,
}
#[derive(Clone, Serialize)]
pub struct AuthMethod {
    pub id: String,
    pub name: String,
}
#[derive(Clone, Serialize, PartialEq, Eq)]
pub struct ModelOption {
    pub id: String,
    pub name: String,
}
#[derive(Clone, Serialize, PartialEq, Eq)]
pub struct ModelSelector {
    pub current: String,
    pub options: Vec<ModelOption>,
}

/// Select support is discovered per ACP session; config IDs and values belong
/// to the agent, never to a provider-specific client mapping.
enum SelectCapability {
    Unsupported,
    Selectable {
        config_id: acp::SessionConfigId,
        selector: ModelSelector,
    },
}
struct Session {
    id: acp::SessionId,
    model: SelectCapability,
    thought_level: SelectCapability,
}
pub enum Chunk {
    Ready,
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
    idle_session: Option<Session>,
    preparing_session: Option<PreparingSession>,
    maintenance: Vec<TransportTask>,
    completed: usize,
}
struct TransportTask(JoinHandle<()>);
struct PreparingSession(JoinHandle<Result<Session, AgentError>>);
struct PreparingConnection(JoinHandle<Result<Connection, AgentError>>);
impl Drop for PreparingConnection {
    fn drop(&mut self) {
        self.0.abort();
    }
}
impl Drop for PreparingSession {
    fn drop(&mut self) {
        self.0.abort();
    }
}
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
            preparing_session: None,
            maintenance: vec![],
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
    async fn session(
        &mut self,
        cwd: PathBuf,
        requested_model: Option<&str>,
        requested_thought_level: Option<&str>,
    ) -> Result<Session, AgentError> {
        let mut session = if let Some(session) = self.idle_session.take() {
            session
        } else if let Some(mut preparing) = self.preparing_session.take() {
            match (&mut preparing.0).await {
                Ok(Ok(session)) => session,
                // Speculative preparation is optional. Retry a transient failure
                // once on demand, with the same bounded request as a cold session.
                _ => Session::open(&self.peer, cwd).await?,
            }
        } else {
            Session::open(&self.peer, cwd).await?
        };
        let result = session
            .configure(&self.peer, requested_model, requested_thought_level)
            .await;
        if let Err(error) = result {
            self.idle_session = Some(session);
            return Err(error);
        }
        Ok(session)
    }
    async fn close_session(&self, id: acp::SessionId) {
        Self::close_on(&self.peer, self.close_supported, id).await;
    }
    async fn close_on(peer: &ConnectionTo<Agent>, supported: bool, id: acp::SessionId) {
        if supported {
            let _ = tokio::time::timeout(
                Duration::from_secs(2),
                peer.send_request(acp::CloseSessionRequest::new(id))
                    .block_task(),
            )
            .await;
        }
    }
    /// Prepare exactly one unused session, without a prompt or conversation history.
    /// Neither preparation nor closing the old session holds the pool's mutex.
    fn prepare_next(
        &mut self,
        cwd: PathBuf,
        model: Option<String>,
        thought: Option<String>,
        previous: acp::SessionId,
    ) {
        self.maintenance.retain(|task| !task.is_finished());
        let peer = self.peer.clone();
        let supported = self.close_supported;
        self.maintenance
            .push(TransportTask(tokio::spawn(async move {
                Self::close_on(&peer, supported, previous).await;
            })));
        if self.completed < 32 {
            let peer = self.peer.clone();
            self.preparing_session = Some(PreparingSession(tokio::spawn(async move {
                let mut session = Session::open(&peer, cwd).await?;
                if let Err(error) = session
                    .configure(&peer, model.as_deref(), thought.as_deref())
                    .await
                {
                    Self::close_on(&peer, supported, session.id).await;
                    return Err(error);
                }
                Ok(session)
            })));
        }
    }
}

#[derive(Clone, Copy)]
enum Setting {
    Model,
    ThoughtLevel,
}
impl Setting {
    fn unsupported(self) -> AgentError {
        match self {
            Self::Model => AgentError::ModelUnsupported,
            Self::ThoughtLevel => AgentError::ThoughtLevelUnsupported,
        }
    }
    fn unavailable(self) -> AgentError {
        match self {
            Self::Model => AgentError::ModelUnavailable,
            Self::ThoughtLevel => AgentError::ThoughtLevelUnavailable,
        }
    }
}
impl SelectCapability {
    fn selector(&self) -> Option<ModelSelector> {
        match self {
            Self::Unsupported => None,
            Self::Selectable { selector, .. } => Some(selector.clone()),
        }
    }
    fn supports(&self, value: &str) -> bool {
        matches!(self, Self::Selectable { selector, .. } if selector.options.iter().any(|option| option.id == value))
    }
}
impl Session {
    async fn open(peer: &ConnectionTo<Agent>, cwd: PathBuf) -> Result<Self, AgentError> {
        let response = tokio::time::timeout(
            Duration::from_secs(30),
            peer.send_request(acp::NewSessionRequest::new(cwd))
                .block_task(),
        )
        .await
        .map_err(|_| AgentError::Timeout)?
        .map_err(map_error)?;
        let mut session = Self {
            id: response.session_id,
            model: SelectCapability::Unsupported,
            thought_level: SelectCapability::Unsupported,
        };
        session.update(response.config_options.unwrap_or_default());
        Ok(session)
    }
    async fn configure(
        &mut self,
        peer: &ConnectionTo<Agent>,
        model: Option<&str>,
        thought: Option<&str>,
    ) -> Result<(), AgentError> {
        if let Some(requested) = model {
            self.select(peer, Setting::Model, requested).await?;
        }
        // A model can remove a remembered value; retain its advertised default.
        if let Some(requested) = thought {
            if self.thought_level.supports(requested) {
                self.select(peer, Setting::ThoughtLevel, requested).await?;
            }
        }
        Ok(())
    }
    fn selector(&self) -> Option<ModelSelector> {
        self.model.selector()
    }
    fn capability(&self, setting: Setting) -> &SelectCapability {
        match setting {
            Setting::Model => &self.model,
            Setting::ThoughtLevel => &self.thought_level,
        }
    }
    fn update(&mut self, options: Vec<acp::SessionConfigOption>) {
        self.model = select_capability(&options, Setting::Model);
        self.thought_level = select_capability(&options, Setting::ThoughtLevel);
    }
    async fn select(
        &mut self,
        peer: &ConnectionTo<Agent>,
        setting: Setting,
        requested: &str,
    ) -> Result<(), AgentError> {
        let SelectCapability::Selectable {
            config_id,
            selector,
        } = self.capability(setting)
        else {
            return Err(setting.unsupported());
        };
        if !selector.options.iter().any(|option| option.id == requested) {
            return Err(setting.unavailable());
        }
        if selector.current == requested {
            return Ok(());
        }
        let response = tokio::time::timeout(
            Duration::from_secs(30),
            peer.send_request(acp::SetSessionConfigOptionRequest::new(
                self.id.clone(),
                config_id.clone(),
                acp::SessionConfigValueId::new(requested.to_owned()),
            ))
            .block_task(),
        )
        .await
        .map_err(|_| AgentError::Timeout)?
        .map_err(map_error)?;
        // ACP returns the full configuration, including options that depend on
        // the changed model or reasoning level.
        self.update(response.config_options);
        if self
            .capability(setting)
            .selector()
            .is_some_and(|selector| selector.current == requested)
        {
            Ok(())
        } else {
            Err(setting.unavailable())
        }
    }
}

fn select_capability(options: &[acp::SessionConfigOption], setting: Setting) -> SelectCapability {
    let Some(option) = options.iter().find(|option| match setting {
        Setting::Model => {
            matches!(
                option.category,
                Some(acp::SessionConfigOptionCategory::Model)
            ) || (option.category.is_none() && option.id.to_string() == "model")
        }
        Setting::ThoughtLevel => matches!(
            option.category,
            Some(acp::SessionConfigOptionCategory::ThoughtLevel)
        ),
    }) else {
        return SelectCapability::Unsupported;
    };
    let acp::SessionConfigKind::Select(select) = &option.kind else {
        return SelectCapability::Unsupported;
    };
    let options: Vec<_> = match &select.options {
        acp::SessionConfigSelectOptions::Ungrouped(options) => options.iter().collect(),
        acp::SessionConfigSelectOptions::Grouped(groups) => groups
            .iter()
            .flat_map(|group| group.options.iter())
            .collect(),
        _ => return SelectCapability::Unsupported,
    };
    let selector = ModelSelector {
        current: select.current_value.to_string(),
        options: options
            .into_iter()
            .map(|option| ModelOption {
                id: option.value.to_string(),
                name: option.name.chars().take(120).collect(),
            })
            .filter(|option| !option.id.is_empty() && option.id.len() <= 256)
            .collect(),
    };
    if selector.options.iter().any(|o| o.id == selector.current) {
        SelectCapability::Selectable {
            config_id: option.id.clone(),
            selector,
        }
    } else {
        SelectCapability::Unsupported
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
struct ConnectionSlot {
    active: Option<Connection>,
    preparing: Option<PreparingConnection>,
}
impl ConnectionSlot {
    async fn finish_preparing(&mut self) -> Result<(), AgentError> {
        if let Some(mut preparing) = self.preparing.take() {
            self.active = Some(
                (&mut preparing.0)
                    .await
                    .map_err(|_| AgentError::Connect)??,
            );
        }
        Ok(())
    }
    async fn acquire(&mut self, launch: Launch) -> Result<&mut Connection, AgentError> {
        // An unsuccessful speculative reconnect is retried once on demand.
        let _ = self.finish_preparing().await;
        if self.active.as_ref().is_none_or(|c| c.task.is_finished()) {
            self.active = None;
            self.active = Some(Connection::open(launch).await?);
        }
        self.active.as_mut().ok_or(AgentError::Connect)
    }

    fn recycle(&mut self, launch: Launch, cwd: PathBuf) {
        // Retire the old transport before preparing its replacement. The slot
        // owns this task, so disconnect/shutdown also cancels the preparation.
        self.active = None;
        self.preparing = Some(PreparingConnection(tokio::spawn(async move {
            let model = launch.model.clone();
            let thought = launch.thought_level.clone();
            let mut connection = Connection::open(launch).await?;
            connection.idle_session = Some(
                connection
                    .session(cwd, model.as_deref(), thought.as_deref())
                    .await?,
            );
            Ok(connection)
        })));
    }
}

#[derive(Default)]
struct Slot {
    connection: Mutex<ConnectionSlot>,
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
        if let Ok(mut connection) = slot.connection.try_lock() {
            if connection
                .preparing
                .as_ref()
                .is_some_and(|task| task.0.is_finished())
            {
                let result = connection.finish_preparing().await;
                let mut status = slot.status.lock().unwrap();
                if let Err(error) = result {
                    *status = Status {
                        message: error.to_string(),
                        ..Status::default()
                    };
                } else if let Some(active) = &connection.active {
                    status.model = active.idle_session.as_ref().and_then(Session::selector);
                    status.thought_level = active
                        .idle_session
                        .as_ref()
                        .and_then(|s| s.thought_level.selector());
                    status.auth_methods = active.auth_methods.clone();
                }
            }
            if connection.preparing.is_none()
                && connection
                    .active
                    .as_ref()
                    .is_none_or(|c| c.task.is_finished())
            {
                slot.status.lock().unwrap().ready = false;
            }
        }
        let status = slot.status.lock().unwrap().clone();
        status
    }
    pub async fn connect(
        &self,
        launch: Launch,
        cwd: PathBuf,
        method: Option<String>,
    ) -> Result<Status, AgentError> {
        let slot = self.slot(&launch.id).await;
        let requested_model = launch.model.clone();
        let requested_thought_level = launch.thought_level.clone();
        let mut guard = slot.connection.try_lock().map_err(|_| AgentError::Busy)?;
        let opened = tokio::select! { result = guard.acquire(launch) => result, _ = slot.cancel.notified() => Err(AgentError::Cancelled) };
        let connection = match opened {
            Ok(connection) => connection,
            Err(error) => {
                *slot.status.lock().unwrap() = Status {
                    message: error.to_string(),
                    ..Status::default()
                };
                return Err(error);
            }
        };
        let operation = async {
            if let Some(method) = method {
                if !connection.auth_methods.iter().any(|m| m.id == method) {
                    return Err(AgentError::Auth);
                }
                // 認証前のセッションを使うと、変更後の認証状態を確認できない。
                if connection.preparing_session.is_some() {
                    if let Ok(session) = connection.session(cwd.clone(), None, None).await {
                        connection.idle_session = Some(session);
                    }
                }
                if let Some(session) = connection.idle_session.take() {
                    connection.close_session(session.id).await;
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
            let session = connection
                .session(
                    cwd,
                    requested_model.as_deref(),
                    requested_thought_level.as_deref(),
                )
                .await?;
            connection.idle_session = Some(session);
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
            model: connection.idle_session.as_ref().and_then(Session::selector),
            thought_level: connection
                .idle_session
                .as_ref()
                .and_then(|s| s.thought_level.selector()),
        };
        *slot.status.lock().unwrap() = status.clone();
        if matches!(
            result,
            Err(AgentError::Timeout | AgentError::Cancelled | AgentError::Connect)
        ) {
            *guard = ConnectionSlot::default();
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
            let requested_model = launch.model.clone();
            let requested_thought_level = launch.thought_level.clone();
            let mut guard = tokio::select! { guard = slot.connection.lock() => guard, _ = tx.closed() => return };
            let result = async {
                let connection = guard.acquire(launch.clone()).await?;
                let session = connection
                    .session(
                        cwd.clone(),
                        requested_model.as_deref(),
                        requested_thought_level.as_deref(),
                    )
                    .await?;
                tx.send(Ok(Chunk::Ready))
                    .await
                    .map_err(|_| AgentError::Cancelled)?;
                connection.overflow.store(false, Ordering::Release);
                *connection.sink.lock().unwrap() = Some((session.id.clone(), tx.clone()));
                let request = connection
                    .peer
                    .send_request(acp::PromptRequest::new(
                        session.id.clone(),
                        vec![acp::ContentBlock::Text(acp::TextContent::new(prompt))],
                    ))
                    .block_task();
                tokio::pin!(request);
                let response = tokio::select! {
                    response = &mut request => response.map_err(map_error),
                    _ = tx.closed() => {
                        let _ = connection.peer.send_notification(acp::CancelNotification::new(session.id.clone()));
                        let _ = tokio::time::timeout(Duration::from_secs(1), &mut request).await;
                        Err(AgentError::Cancelled)
                    }
                    _ = tokio::time::sleep(Duration::from_secs(90)) => {
                        let _ = connection.peer.send_notification(acp::CancelNotification::new(session.id.clone()));
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
                    model: session.selector(),
                    thought_level: session.thought_level.selector(),
                };
                connection.completed += 1;
                Ok::<_, AgentError>(session.id)
            };
            let result = tokio::select! { result = result => result, _ = tx.closed() => Err(AgentError::Cancelled), _ = slot.cancel.notified() => Err(AgentError::Cancelled) };
            // A cancelled/failed session never becomes the next request's transport.
            if let Err(error) = &result {
                if let Some(connection) = &guard.active {
                    if let Some((session, _)) = connection.sink.lock().unwrap().take() {
                        let _ = connection
                            .peer
                            .send_notification(acp::CancelNotification::new(session));
                    }
                }
                *guard = ConnectionSlot::default();
                *slot.status.lock().unwrap() = Status {
                    message: error.to_string(),
                    ..Status::default()
                };
            }
            match result {
                Ok(previous) => {
                    if guard.active.as_ref().is_some_and(|c| c.completed >= 32) {
                        guard.recycle(launch, cwd);
                    } else if let Some(connection) = guard.active.as_mut() {
                        connection.prepare_next(
                            cwd,
                            requested_model,
                            requested_thought_level,
                            previous,
                        );
                    }
                    let _ = tx.send(Ok(Chunk::Done)).await;
                }
                Err(error) => {
                    let _ = tx.send(Err(error)).await;
                }
            }
        });
        rx
    }
    pub async fn select_model(
        &self,
        launch: Launch,
        cwd: PathBuf,
        model: &str,
    ) -> Result<Status, AgentError> {
        self.select(launch, cwd, Setting::Model, model).await
    }
    pub async fn select_thought_level(
        &self,
        launch: Launch,
        cwd: PathBuf,
        value: &str,
    ) -> Result<Status, AgentError> {
        self.select(launch, cwd, Setting::ThoughtLevel, value).await
    }
    async fn select(
        &self,
        launch: Launch,
        cwd: PathBuf,
        setting: Setting,
        value: &str,
    ) -> Result<Status, AgentError> {
        let slot = self.slot(&launch.id).await;
        let mut guard = slot.connection.try_lock().map_err(|_| AgentError::Busy)?;
        let model = match setting {
            Setting::Model => Some(value),
            _ => launch.model.as_deref(),
        }
        .map(str::to_owned);
        let thought_level = launch.thought_level.clone();
        let connection = guard.acquire(launch).await?;
        let result = async {
            let mut session = connection
                .session(cwd, model.as_deref(), thought_level.as_deref())
                .await?;
            let result = session.select(&connection.peer, setting, value).await;
            connection.idle_session = Some(session);
            result
        }
        .await;
        let mut status = Status {
            ready: connection.idle_session.is_some(),
            auth_methods: connection.auth_methods.clone(),
            message: match &result {
                Ok(()) => "設定を変更しました。".into(),
                Err(error) => error.to_string(),
            },
            model: connection.idle_session.as_ref().and_then(Session::selector),
            thought_level: connection
                .idle_session
                .as_ref()
                .and_then(|s| s.thought_level.selector()),
        };
        if matches!(
            result,
            Err(AgentError::Timeout
                | AgentError::Connect
                | AgentError::Auth
                | AgentError::Cancelled)
        ) {
            // An uncertain remote outcome must not be reused as confirmed settings.
            *guard = ConnectionSlot::default();
            status.ready = false;
            status.model = None;
            status.thought_level = None;
        }
        *slot.status.lock().unwrap() = status.clone();
        result?;
        Ok(status)
    }
    pub async fn disconnect(&self, id: &str) -> Result<(), AgentError> {
        let slot = self.slot(id).await;
        let mut guard = slot.connection.try_lock().map_err(|_| AgentError::Busy)?;
        *guard = ConnectionSlot::default();
        *slot.status.lock().unwrap() = Status::default();
        Ok(())
    }
    pub async fn adopt(&self, id: &str, verified: Self) {
        if let Some(slot) = verified.slots.lock().await.remove(id) {
            // 呼出側が更新操作を直列化し、会議開始を止めている間に切り替える。
            let previous = self.slots.lock().await.insert(id.into(), slot);
            if let Some(previous) = previous {
                *previous.connection.lock().await = ConnectionSlot::default();
            }
        }
    }
    pub async fn shutdown(&self) {
        for slot in self.slots.lock().await.values() {
            slot.cancel.notify_one();
            *slot.connection.lock().await = ConnectionSlot::default();
        }
    }
}
