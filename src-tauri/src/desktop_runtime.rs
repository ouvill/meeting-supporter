//! Lifecycle of the in-process backend serving the existing desktop UI.
use meeting_desktop_runtime::{Config, Server};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
};
use tauri::{AppHandle, Manager};

#[derive(Default)]
pub struct DesktopState {
    server: Mutex<Option<Server>>,
    closing: AtomicBool,
    exit_ready: AtomicBool,
}
pub fn connection(app: &AppHandle) -> Option<(u16, String)> {
    let state = app.state::<DesktopState>();
    let guard = state.server.lock().unwrap_or_else(|e| e.into_inner());
    guard
        .as_ref()
        .filter(|s| s.is_running())
        .map(|s| (s.port, s.token.clone()))
}
fn configured(name: &str, fallback: PathBuf) -> PathBuf {
    std::env::var_os(name)
        .map(PathBuf::from)
        .unwrap_or(fallback)
}
pub fn start(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        let result = async {
            let data_dir = app
                .path()
                .app_data_dir()
                .map_err(|_| "アプリの保存先を取得できません。".to_string())?;
            let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
            let resources = app
                .path()
                .resource_dir()
                .map_err(|_| "実行ファイルの配置を確認してください。".to_string())?;
            let models = if cfg!(debug_assertions) {
                root.join("test/rust-native-backend/target/models")
            } else {
                data_dir.join("models")
            };
            let audio = if cfg!(debug_assertions) {
                root.join("crates/meeting-audio-runtime/target/release/meeting-audio-runtime")
            } else {
                resources.join("native/meeting-audio-runtime")
            };
            let speech = if cfg!(debug_assertions) {
                root.join("test/rust-native-backend/target/release/meeting-native-backend")
            } else {
                resources.join("native/meeting-native-backend")
            };
            let punctuation = match std::env::var_os("MEETING_REAZON_PUNCTUATION") {
                Some(path) => Some(PathBuf::from(path)),
                None => {
                    let path = models.join("punctuation-bert");
                    path.is_dir().then_some(path)
                }
            };
            let legacy = meeting_desktop_runtime::settings::FileSecrets {
                path: data_dir.join("secrets.toml"),
            };
            let secrets: std::sync::Arc<dyn meeting_desktop_runtime::settings::Secrets> =
                if std::env::var("SECRET_STORE_BACKEND").is_ok_and(|v| v == "file") {
                    std::sync::Arc::new(legacy)
                } else {
                    std::sync::Arc::new(meeting_desktop_runtime::settings::MigratingSecrets {
                        primary: std::sync::Arc::new(crate::desktop_secrets::OsSecrets),
                        legacy,
                    })
                };
            Server::start_with_secrets(
                Config {
                    agent_updates: true,
                    data_dir,
                    audio_worker: configured("MEETING_AUDIO_WORKER", audio),
                    speech_worker: configured("MEETING_REAZON_WORKER", speech),
                    model: std::env::var_os("MEETING_REAZON_MODEL").map(PathBuf::from),
                    legacy_model: cfg!(debug_assertions).then(|| models.join("reazonspeech")),
                    hub_cache: meeting_desktop_runtime::models::default_hub_cache()
                        .map_err(|_| "モデルの保存先を取得できません。".to_string())?,
                    hub_offline: std::env::var("HF_HUB_OFFLINE").is_ok_and(|v| {
                        matches!(v.to_ascii_uppercase().as_str(), "1" | "ON" | "YES" | "TRUE")
                    }),
                    punctuation,
                    python_worker: configured("MEETING_PYTHON_WORKER", {
                        let directory = if cfg!(debug_assertions) {
                            root.join("generated/python-worker/dist/meeting-python-worker")
                        } else {
                            resources.join("python-worker")
                        };
                        directory.join(if cfg!(windows) {
                            "meeting-python-worker.exe"
                        } else {
                            "meeting-python-worker"
                        })
                    }),
                },
                secrets,
            )
            .await
            .map_err(|e| e.to_string())
        }
        .await;
        match result {
            Ok(server) => {
                let state = app.state::<DesktopState>();
                if state.closing.load(Ordering::Acquire) {
                    let _ = server.shutdown().await;
                    return;
                }
                *state.server.lock().unwrap_or_else(|e| e.into_inner()) = Some(server);
                let _ =
                    crate::state::set_bootstrap_status(&app, "running", "Rust backend is ready.");
                eprintln!("[backend] Rust in-process backend is ready (Python worker starts only on demand).");
            }
            Err(error) => {
                let _ = crate::state::set_bootstrap_status(&app, "failed", error);
            }
        }
    });
}
pub fn request_exit(app: &AppHandle) {
    let state = app.state::<DesktopState>();
    if state.closing.swap(true, Ordering::AcqRel) {
        return;
    }
    let server = state
        .server
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .take();
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Some(server) = server {
            if let Err(error) = server.shutdown().await {
                eprintln!("[backend] shutdown: {error}");
            }
        }
        app.state::<DesktopState>()
            .exit_ready
            .store(true, Ordering::Release);
        app.exit(0);
    });
}
pub fn exit_ready(app: &AppHandle) -> bool {
    app.state::<DesktopState>()
        .exit_ready
        .load(Ordering::Acquire)
}
