//! Model preparation is owned by Rust. Shared Hugging Face files are never deleted wholesale.
mod catalog;
mod hub;
#[cfg(test)]
mod tests;
mod transfer;
pub(crate) use catalog::{Backend, Key, Language, Request};
use serde::Serialize;
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
};
use thiserror::Error;
use tokio::{sync::watch, task::JoinHandle};

/// Resolve the same hub directory as Python huggingface_hub, including its legacy override.
pub fn default_hub_cache() -> Result<PathBuf, std::io::Error> {
    cache_path(
        |key| std::env::var_os(key).map(PathBuf::from),
        dirs::home_dir(),
    )
    .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::NotFound, "home directory unavailable"))
}
fn cache_path(get: impl Fn(&str) -> Option<PathBuf>, home: Option<PathBuf>) -> Option<PathBuf> {
    let path = get("HF_HUB_CACHE")
        .or_else(|| get("HUGGINGFACE_HUB_CACHE"))
        .or_else(|| get("HF_HOME").map(|p| p.join("hub")))
        .or_else(|| get("XDG_CACHE_HOME").map(|p| p.join("huggingface/hub")))
        .or_else(|| home.as_ref().map(|p| p.join(".cache/huggingface/hub")))?;
    if let Ok(relative) = path.strip_prefix("~") {
        home.map(|p| p.join(relative))
    } else {
        Some(path)
    }
}
#[derive(Debug, Error)]
pub enum ModelError {
    #[error("モデルの選択が不正です。ReazonSpeechは日本語にのみ対応しています。")]
    Selection,
    #[error("モデルを取得してから音声認識を準備してください。")]
    NotReady,
    #[error("モデルを取得できませんでした。ネットワーク接続を確認してください。")]
    Network,
    #[error("モデルの取得がオフライン設定で無効になっています。")]
    Offline,
    #[error("取得したモデルの検証に失敗しました。再度取得してください。")]
    Checksum,
    #[error("モデルの形式またはサイズが正しくありません。")]
    Archive,
    #[error("モデルの取得をキャンセルしました。")]
    Cancelled,
    #[error("モデルを保存できませんでした。空き容量と権限を確認してください。")]
    Io(#[from] std::io::Error),
    #[error("モデルの取得処理に失敗しました。")]
    Task,
}
#[derive(Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum State {
    Missing,
    Downloading,
    Ready,
    Failed,
    Cancelled,
}
#[derive(Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Phase {
    Idle,
    Downloading,
    Verifying,
    Ready,
}
#[derive(Clone, Copy, Debug, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ErrorCode {
    Network,
    DiskFull,
    Permission,
    Checksum,
    Archive,
    Cancelled,
    Unknown,
}
impl ModelError {
    fn code(&self) -> ErrorCode {
        match self {
            Self::Network | Self::Offline => ErrorCode::Network,
            Self::Checksum => ErrorCode::Checksum,
            Self::Archive => ErrorCode::Archive,
            Self::Cancelled => ErrorCode::Cancelled,
            Self::Io(e) if e.kind() == std::io::ErrorKind::StorageFull => ErrorCode::DiskFull,
            Self::Io(e) if e.kind() == std::io::ErrorKind::PermissionDenied => {
                ErrorCode::Permission
            }
            _ => ErrorCode::Unknown,
        }
    }
}
#[derive(Clone, Serialize)]
pub(crate) struct Status {
    pub backend: Backend,
    pub model_id: &'static str,
    pub state: State,
    pub phase: Phase,
    pub language: Language,
    pub downloaded_bytes: u64,
    pub total_bytes: Option<u64>,
    pub progress_percent: Option<u64>,
    pub model_path: Option<PathBuf>,
    pub storage_path: PathBuf,
    pub error_code: Option<ErrorCode>,
    pub message: String,
    pub retryable: bool,
    pub cancelable: bool,
}
struct Slot {
    status: Status,
    cancel: watch::Sender<bool>,
    task: Option<JoinHandle<()>>,
}
pub(crate) struct Manager {
    cache: PathBuf,
    local_reazon: Option<PathBuf>,
    legacy_reazon: Option<PathBuf>,
    offline: bool,
    source: transfer::Source,
    slots: Mutex<HashMap<Key, Slot>>,
    closed: AtomicBool,
}
impl Manager {
    pub fn new(config: &crate::Config) -> Arc<Self> {
        Arc::new(Self {
            cache: config.hub_cache.clone(),
            local_reazon: config.model.clone(),
            legacy_reazon: config.legacy_model.clone(),
            offline: config.hub_offline,
            source: transfer::Source::default(),
            slots: Mutex::new(HashMap::new()),
            closed: AtomicBool::new(false),
        })
    }
    fn initial(&self, key: Key, language: Language) -> Status {
        Status {
            backend: key.backend(),
            model_id: key.id(),
            state: State::Missing,
            phase: Phase::Idle,
            language,
            downloaded_bytes: 0,
            total_bytes: None,
            progress_percent: None,
            model_path: None,
            storage_path: match key {
                Key::Reazon => self
                    .local_reazon
                    .clone()
                    .unwrap_or_else(|| self.cache.clone()),
                _ => self.cache.clone(),
            },
            error_code: None,
            message: "モデルはまだありません。取得してから音声認識を準備してください。".into(),
            retryable: true,
            cancelable: false,
        }
    }
    fn cached(&self, key: Key) -> Option<PathBuf> {
        match key {
            Key::Reazon => {
                if let Some(path) = &self.local_reazon {
                    return valid_model(path, key).then(|| path.clone());
                }
                hub::cached(&self.cache, key).or_else(|| {
                    self.legacy_reazon
                        .as_ref()
                        .filter(|p| valid_model(p, key))
                        .cloned()
                })
            }
            Key::Whisper(_) => hub::cached(&self.cache, key),
        }
    }
    fn ready(&self, key: Key, language: Language, path: PathBuf) -> Status {
        let mut status = self.initial(key, language);
        status.state = State::Ready;
        status.phase = Phase::Ready;
        status.model_path = Some(path);
        status.progress_percent = Some(100);
        status.retryable = false;
        status.message = "モデルを利用できます。".into();
        status
    }
    pub fn status(&self, request: &Request) -> Result<Status, ModelError> {
        let key = request.key()?;
        let slots = self.slots.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(slot) = slots.get(&key) {
            if slot.status.state == State::Downloading {
                let mut s = slot.status.clone();
                s.language = request.language;
                return Ok(s);
            }
        }
        if let Some(path) = self.cached(key) {
            return Ok(self.ready(key, request.language, path));
        }
        let mut status = slots
            .get(&key)
            .filter(|s| s.status.state != State::Ready)
            .map(|s| s.status.clone())
            .unwrap_or_else(|| self.initial(key, request.language));
        status.language = request.language;
        Ok(status)
    }
    pub fn start(self: &Arc<Self>, request: &Request) -> Result<Status, ModelError> {
        let key = request.key()?;
        let mut slots = self.slots.lock().unwrap_or_else(|e| e.into_inner());
        if self.closed.load(Ordering::Acquire) {
            return Err(ModelError::Cancelled);
        }
        if let Some(slot) = slots.get(&key) {
            if slot.status.state == State::Downloading {
                let mut s = slot.status.clone();
                s.language = request.language;
                return Ok(s);
            }
        }
        if let Some(path) = self.cached(key) {
            return Ok(self.ready(key, request.language, path));
        }
        let mut status = self.initial(key, request.language);
        if key == Key::Reazon && self.local_reazon.is_some() {
            status.state = State::Failed;
            status.error_code = Some(ErrorCode::Unknown);
            status.retryable = false;
            status.message =
                "指定されたローカルモデルが不完全です。モデルの配置を確認してください。".into();
        } else if self.offline {
            status.state = State::Failed;
            status.error_code = Some(ErrorCode::Network);
            status.message = ModelError::Offline.to_string();
        }
        if status.state == State::Failed {
            let (cancel, _) = watch::channel(false);
            slots.insert(
                key,
                Slot {
                    status: status.clone(),
                    cancel,
                    task: None,
                },
            );
            return Ok(status);
        }
        status.state = State::Downloading;
        status.phase = Phase::Downloading;
        status.cancelable = true;
        status.retryable = false;
        status.message = "モデルを取得しています。".into();
        let (cancel, rx) = watch::channel(false);
        let manager = self.clone();
        let task = tokio::spawn(async move {
            let progress = Progress {
                manager: manager.clone(),
                key,
            };
            let result = hub::download(&manager, key, progress, rx).await;
            manager.finish(key, result);
        });
        slots.insert(
            key,
            Slot {
                status: status.clone(),
                cancel,
                task: Some(task),
            },
        );
        Ok(status)
    }
    pub fn cancel(&self, request: &Request) -> Result<Status, ModelError> {
        let key = request.key()?;
        {
            let mut slots = self.slots.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(slot) = slots.get_mut(&key) {
                if slot.status.state == State::Downloading {
                    let _ = slot.cancel.send(true);
                    slot.status.cancelable = false;
                    slot.status.message = "モデルの取得を停止しています。".into();
                }
            }
        }
        self.status(request)
    }
    fn finish(&self, key: Key, result: Result<PathBuf, ModelError>) {
        let mut slots = self.slots.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(slot) = slots.get_mut(&key) {
            match result {
                Ok(path) => {
                    let bytes = slot.status.downloaded_bytes;
                    let total = slot.status.total_bytes;
                    slot.status = self.ready(key, slot.status.language, path);
                    slot.status.downloaded_bytes = bytes;
                    slot.status.total_bytes = total;
                }
                Err(error) => {
                    slot.status.state = if matches!(error, ModelError::Cancelled) {
                        State::Cancelled
                    } else {
                        State::Failed
                    };
                    slot.status.phase = Phase::Idle;
                    slot.status.error_code = Some(error.code());
                    slot.status.message = error.to_string();
                    slot.status.retryable = true;
                    slot.status.cancelable = false;
                    slot.status.model_path = None;
                }
            }
        }
    }
    pub fn reazon_path(&self) -> Result<PathBuf, ModelError> {
        self.cached(Key::Reazon).ok_or(ModelError::NotReady)
    }
    pub async fn shutdown(&self) {
        let tasks = {
            let mut slots = self.slots.lock().unwrap_or_else(|e| e.into_inner());
            self.closed.store(true, Ordering::Release);
            slots
                .values_mut()
                .filter_map(|s| {
                    let _ = s.cancel.send(true);
                    s.task.take()
                })
                .collect::<Vec<_>>()
        };
        for task in tasks {
            let _ = task.await;
        }
    }
}
#[derive(Clone)]
struct Progress {
    manager: Arc<Manager>,
    key: Key,
}
impl Progress {
    fn update(&self, phase: Phase, bytes: u64, total: Option<u64>) {
        let mut slots = self.manager.slots.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(slot) = slots.get_mut(&self.key) {
            slot.status.phase = phase;
            slot.status.downloaded_bytes = bytes;
            slot.status.total_bytes = total;
            slot.status.progress_percent = total
                .filter(|v| *v > 0)
                .map(|n| (bytes.saturating_mul(100) / n).min(99));
        }
    }
}
fn valid_model(path: &Path, key: Key) -> bool {
    key.required()
        .iter()
        .all(|file| std::fs::metadata(path.join(file)).is_ok_and(|m| m.is_file() && m.len() > 0))
}
fn check_cancel(cancel: &watch::Receiver<bool>) -> Result<(), ModelError> {
    if *cancel.borrow() {
        Err(ModelError::Cancelled)
    } else {
        Ok(())
    }
}
