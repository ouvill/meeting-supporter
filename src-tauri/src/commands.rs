use serde::Serialize;
use tauri::{Manager, State, Window};

use crate::error::AppError;
use crate::process::{
    refresh_backend_state, BackendCrashInfo, BackendProcess, BackendState,
};
use crate::state::{BootstrapState, BootstrapStateData};

#[derive(Debug, Serialize)]
pub struct BackendBootstrapSnapshot {
    pub phase: String,
    pub message: String,
    pub running: bool,
    pub port: Option<u16>,
    pub auth_token: Option<String>,
    pub crash: Option<BackendCrashInfo>,
}

fn build_backend_bootstrap_snapshot(
    bootstrap: &BootstrapStateData,
    backend: &mut BackendProcess,
) -> BackendBootstrapSnapshot {
    let running = refresh_backend_state(backend);
    BackendBootstrapSnapshot {
        phase: bootstrap.phase.clone(),
        message: bootstrap.message.clone(),
        running,
        port: running.then_some(backend.port).flatten(),
        auth_token: running.then(|| backend.auth_token.clone()).flatten(),
        crash: backend.crash_info.clone(),
    }
}

#[tauri::command]
pub fn get_backend_bootstrap_snapshot(
    bootstrap_state: State<BootstrapState>,
    backend_state: State<BackendState>,
) -> Result<BackendBootstrapSnapshot, AppError> {
    // Hold both guards while refreshing so every field comes from one point-in-time view.
    let bootstrap = bootstrap_state
        .lock()
        .map_err(|e| AppError::MutexPoison(e.to_string()))?;
    let mut backend = backend_state
        .lock()
        .map_err(|e| AppError::MutexPoison(e.to_string()))?;
    Ok(build_backend_bootstrap_snapshot(&bootstrap, &mut backend))
}

#[tauri::command]
pub fn set_assistant_window_visible(window: Window, visible: bool) -> Result<(), AppError> {
    let assistant = window
        .app_handle()
        .get_webview_window("assistant")
        .ok_or_else(|| AppError::Other("assistant window not found".to_string()))?;

    if visible {
        assistant.show()?;
        assistant.set_focus()?;
    } else {
        assistant.hide()?;
    }

    Ok(())
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_fails_closed_and_preserves_crash_details_when_not_running() {
        let bootstrap = BootstrapStateData {
            phase: "running".into(),
            message: "Backend is ready.".into(),
        };
        let crash = BackendCrashInfo {
            unexpected: true,
            exit_code: Some(137),
            signal: None,
            message: "Backend process terminated unexpectedly.".into(),
        };
        let mut backend = BackendProcess::none();
        backend.port = Some(49152);
        backend.auth_token = Some("must-not-leak".into());
        backend.crash_info = Some(crash);

        let snapshot = build_backend_bootstrap_snapshot(&bootstrap, &mut backend);

        assert!(!snapshot.running);
        assert_eq!(snapshot.port, None);
        assert_eq!(snapshot.auth_token, None);
        let crash = snapshot.crash.expect("crash details should remain visible");
        assert!(crash.unexpected);
        assert_eq!(crash.exit_code, Some(137));
    }

    #[test]
    fn snapshot_serializes_as_the_frontend_wire_contract() {
        let bootstrap = BootstrapStateData {
            phase: "starting".into(),
            message: "Starting backend...".into(),
        };
        let mut backend = BackendProcess::none();

        let value = serde_json::to_value(build_backend_bootstrap_snapshot(
            &bootstrap,
            &mut backend,
        ))
        .expect("snapshot should serialize");

        assert_eq!(
            value,
            serde_json::json!({
                "phase": "starting",
                "message": "Starting backend...",
                "running": false,
                "port": null,
                "auth_token": null,
                "crash": null,
            })
        );
    }
}
