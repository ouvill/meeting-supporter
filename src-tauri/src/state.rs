use std::sync::Mutex;
use tauri::{AppHandle, Manager};

use crate::error::AppError;

#[derive(Clone)]
pub struct BootstrapStateData {
    pub phase: String,
    pub message: String,
}

impl BootstrapStateData {
    pub fn new() -> Self {
        Self {
            phase: "initializing".into(),
            message: "Preparing Python environment...".into(),
        }
    }
}

impl Default for BootstrapStateData {
    fn default() -> Self {
        Self::new()
    }
}

pub type BootstrapState = Mutex<BootstrapStateData>;

pub fn set_bootstrap_status(
    app: &AppHandle,
    phase: &str,
    message: impl Into<String>,
) -> Result<(), AppError> {
    let state_handle = app.state::<BootstrapState>();
    let mut state = state_handle
        .lock()
        .map_err(|e| AppError::MutexPoison(e.to_string()))?;
    state.phase = phase.to_string();
    state.message = message.into();
    Ok(())
}
