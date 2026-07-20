use serde::Serialize;
use tauri::{Manager, State, Window};

use crate::error::AppError;
use crate::process::{refresh_backend_state, BackendCrashInfo, BackendState};
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
pub fn get_api_port(state: State<BackendState>) -> Result<Option<u16>, AppError> {
    let mut backend = state
        .lock()
        .map_err(|e| AppError::MutexPoison(e.to_string()))?;
    if refresh_backend_state(&mut backend) {
        Ok(backend.port)
    } else {
        Ok(None)
    }
}

#[tauri::command]
pub fn get_api_auth_token(state: State<BackendState>) -> Result<Option<String>, AppError> {
    let mut backend = state
        .lock()
        .map_err(|e| AppError::MutexPoison(e.to_string()))?;
    if refresh_backend_state(&mut backend) {
        Ok(backend.auth_token.clone())
    } else {
        Ok(None)
    }
}

#[tauri::command]
pub fn is_backend_running(state: State<BackendState>) -> Result<bool, AppError> {
    let mut backend = state
        .lock()
        .map_err(|e| AppError::MutexPoison(e.to_string()))?;
    Ok(refresh_backend_state(&mut backend))
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

/// 直近のバックエンド異常終了情報を取得する。
/// クラッシュが検出されていない場合は `None` を返す。
///
/// 呼び出しごとに `refresh_backend_state` を実行し、
/// プロセス終了の検出をこのコマンド単体で完結させる。
/// （`is_backend_running` / `get_api_port` の副作用に依存しない）
#[tauri::command]
pub fn get_backend_crash_info(
    state: State<BackendState>,
) -> Result<Option<BackendCrashInfo>, AppError> {
    let mut backend = state
        .lock()
        .map_err(|e| AppError::MutexPoison(e.to_string()))?;
    refresh_backend_state(&mut backend);
    Ok(backend.crash_info.clone())
}
