use serde::Serialize;
use tauri::{Manager, State, Window};

use crate::error::AppError;
use crate::state::BootstrapState;

#[derive(Serialize)]
pub struct BootstrapStatus {
    pub phase: String,
    pub message: String,
}

#[tauri::command]
pub fn get_backend_bootstrap_status(
    state: State<BootstrapState>,
) -> Result<BootstrapStatus, AppError> {
    let current = state
        .lock()
        .map_err(|e| AppError::MutexPoison(e.to_string()))?
        .clone();
    Ok(BootstrapStatus {
        phase: current.phase,
        message: current.message,
    })
}

#[tauri::command]
pub fn get_api_port(app: tauri::AppHandle) -> Option<u16> {
    crate::desktop_runtime::connection(&app).map(|v| v.0)
}

#[tauri::command]
pub fn get_api_auth_token(app: tauri::AppHandle) -> Option<String> {
    crate::desktop_runtime::connection(&app).map(|v| v.1)
}

#[tauri::command]
pub fn is_backend_running(app: tauri::AppHandle) -> bool {
    crate::desktop_runtime::connection(&app).is_some()
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
