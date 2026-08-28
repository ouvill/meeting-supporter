mod client;
mod oidc;
mod token_store;

#[cfg(test)]
mod tests;

use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::process::BackendState;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_opener::OpenerExt;
use url::Url;

use self::oidc::{PendingAuthorization, RefreshError, TokenResponse};
use self::token_store::{read_refresh_token, remove_refresh_token, store_refresh_token};
use crate::error::AppError;

pub use self::client::{
    managed_api_base_url, ManagedAccount, ManagedAvailability, ManagedCapability,
    ManagedEntitlement, ManagedPlan, ManagedQuota,
};

#[derive(Default)]
pub struct ManagedAuthState {
    data: Mutex<ManagedAuthStateData>,
    refresh: Mutex<()>,
}

#[derive(Default)]
struct ManagedAuthStateData {
    pending: Option<PendingAuthorization>,
    session: Option<AccessSession>,
    last_reason: Option<String>,
    generation: u64,
}

#[derive(Clone)]
struct AccessSession {
    token: String,
    expires_at: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct ManagedAuthStatus {
    pub authenticated: bool,
    pub reason: String,
}

fn refresh_tokens(state: &ManagedAuthState) -> Result<String, AppError> {
    let _refresh = state
        .refresh
        .lock()
        .map_err(|error| AppError::MutexPoison(error.to_string()))?;
    {
        let auth = state
            .data
            .lock()
            .map_err(|error| AppError::MutexPoison(error.to_string()))?;
        if let Some(session) = auth
            .session
            .as_ref()
            .filter(|session| session.expires_at > now_seconds().saturating_add(30))
        {
            return Ok(session.token.clone());
        }
    }
    let refresh_token = read_refresh_token()?;
    let tokens = match oidc::refresh_authorization(&refresh_token) {
        Ok(tokens) => tokens,
        Err(RefreshError::Rejected) => {
            let _ = remove_refresh_token();
            return Err(AppError::Other("sign in required".into()));
        }
        Err(RefreshError::Other(error)) => return Err(error),
    };
    install_tokens(state, tokens)?;
    let auth = state
        .data
        .lock()
        .map_err(|error| AppError::MutexPoison(error.to_string()))?;
    auth.session
        .as_ref()
        .map(|session| session.token.clone())
        .ok_or_else(|| AppError::Other("sign in required".into()))
}

fn now_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn install_tokens(state: &ManagedAuthState, tokens: TokenResponse) -> Result<(), AppError> {
    if tokens.access_token.is_empty() {
        return Err(AppError::Other("access token missing".into()));
    }
    if let Some(refresh_token) = tokens.refresh_token.as_deref() {
        store_refresh_token(refresh_token)?;
    }
    let mut auth = state
        .data
        .lock()
        .map_err(|error| AppError::MutexPoison(error.to_string()))?;
    auth.session = Some(AccessSession {
        token: tokens.access_token,
        expires_at: now_seconds().saturating_add(tokens.expires_in.unwrap_or(300)),
    });
    auth.last_reason = None;
    Ok(())
}

fn local_http_agent() -> ureq::Agent {
    ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(5)))
        .build()
        .new_agent()
}

fn sync_python_session(app: &AppHandle, session: Option<&AccessSession>) -> Result<(), AppError> {
    let (port, backend_token, capability) = {
        let backend = app.state::<BackendState>();
        let backend = backend
            .lock()
            .map_err(|error| AppError::MutexPoison(error.to_string()))?;
        (
            backend.port,
            backend.auth_token.clone(),
            backend.managed_session_capability.clone(),
        )
    };
    let (Some(port), Some(backend_token), Some(capability)) = (port, backend_token, capability)
    else {
        return Err(AppError::Other("managed session bridge unavailable".into()));
    };
    let url = format!("http://127.0.0.1:{port}/internal/managed-session");
    let agent = local_http_agent();
    let request = if let Some(session) = session {
        let api_base_url = managed_api_base_url()?
            .as_str()
            .trim_end_matches('/')
            .to_owned();
        agent
            .put(&url)
            .header("authorization", &format!("Bearer {backend_token}"))
            .header("x-managed-session-capability", &capability)
            .send_json(serde_json::json!({
                "access_token": session.token,
                "expires_at": session.expires_at,
                "api_base_url": api_base_url,
            }))
    } else {
        agent
            .delete(&url)
            .header("authorization", &format!("Bearer {backend_token}"))
            .header("x-managed-session-capability", &capability)
            .call()
    };
    request
        .map(|_| ())
        .map_err(|_| AppError::Other("managed session bridge unavailable".into()))
}

pub fn sync_current_python_session(app: &AppHandle) -> Result<(), AppError> {
    let state = app.state::<ManagedAuthState>();
    let _ = access_token(&state)?;
    let session = state
        .data
        .lock()
        .map_err(|error| AppError::MutexPoison(error.to_string()))?
        .session
        .clone()
        .ok_or_else(|| AppError::Other("access token missing".into()))?;
    sync_python_session(app, Some(&session))
}

pub fn start_managed_session_sync(app: AppHandle) {
    std::thread::spawn(move || loop {
        let _ = sync_current_python_session(&app);
        std::thread::sleep(Duration::from_secs(30));
    });
}

fn emit_auth_changed(app: &AppHandle, status: &ManagedAuthStatus) {
    let _ = app.emit("managed-auth-changed", status);
}

pub fn handle_deep_link(app: AppHandle, url: Url) {
    let pending_and_code = {
        let state = app.state::<ManagedAuthState>();
        let mut auth = match state.data.lock() {
            Ok(auth) => auth,
            Err(_) => return,
        };
        let Some(pending) = auth.pending.as_ref() else {
            return;
        };
        match oidc::validate_callback(&url, pending) {
            Ok(code) => auth.pending.take().map(|pending| (pending, code)),
            Err(_) => None,
        }
    };
    let Some((pending, code)) = pending_and_code else {
        return;
    };
    std::thread::spawn(move || {
        let result = oidc::exchange_authorization_code(&pending, &code).and_then(|tokens| {
            let state = app.state::<ManagedAuthState>();
            let _refresh = state
                .refresh
                .lock()
                .map_err(|error| AppError::MutexPoison(error.to_string()))?;
            {
                let auth = state
                    .data
                    .lock()
                    .map_err(|error| AppError::MutexPoison(error.to_string()))?;
                if auth.generation != pending.generation {
                    return Err(AppError::Other("authorization superseded".into()));
                }
            }
            install_tokens(&state, tokens)?;
            let session = state
                .data
                .lock()
                .map_err(|error| AppError::MutexPoison(error.to_string()))?
                .session
                .clone()
                .ok_or_else(|| AppError::Other("access token missing".into()))?;
            sync_python_session(&app, Some(&session))
        });
        let status = {
            let state = app.state::<ManagedAuthState>();
            let mut auth = state.data.lock().unwrap_or_else(|error| error.into_inner());
            match result {
                Ok(()) => ManagedAuthStatus {
                    authenticated: true,
                    reason: "ready".into(),
                },
                Err(_) => {
                    if auth.generation == pending.generation && auth.session.is_none() {
                        auth.last_reason = Some("authentication_failed".into());
                    }
                    ManagedAuthStatus {
                        authenticated: false,
                        reason: "authentication_failed".into(),
                    }
                }
            }
        };
        emit_auth_changed(&app, &status);
    });
}

#[tauri::command]
pub fn managed_auth_start(
    app: AppHandle,
    state: State<ManagedAuthState>,
) -> Result<ManagedAuthStatus, AppError> {
    let (mut pending, authorization_url) = oidc::begin_authorization()?;
    {
        let _refresh = state
            .refresh
            .lock()
            .map_err(|error| AppError::MutexPoison(error.to_string()))?;
        let mut auth = state
            .data
            .lock()
            .map_err(|error| AppError::MutexPoison(error.to_string()))?;
        auth.generation = auth.generation.wrapping_add(1);
        pending.generation = auth.generation;
        auth.pending = Some(pending);
        auth.last_reason = Some("browser_open".into());
    }
    app.opener()
        .open_url(authorization_url.as_str(), None::<&str>)
        .map_err(|_| AppError::Other("could not open system browser".into()))?;
    Ok(ManagedAuthStatus {
        authenticated: false,
        reason: "browser_open".into(),
    })
}

#[tauri::command]
pub fn managed_auth_status(state: State<ManagedAuthState>) -> Result<ManagedAuthStatus, AppError> {
    if access_token(&state).is_ok() {
        return Ok(ManagedAuthStatus {
            authenticated: true,
            reason: "ready".into(),
        });
    }
    let auth = state
        .data
        .lock()
        .map_err(|error| AppError::MutexPoison(error.to_string()))?;
    Ok(ManagedAuthStatus {
        authenticated: false,
        reason: auth
            .last_reason
            .clone()
            .unwrap_or_else(|| "sign_in_required".into()),
    })
}

#[tauri::command]
pub fn managed_auth_logout(
    app: AppHandle,
    state: State<ManagedAuthState>,
) -> Result<ManagedAuthStatus, AppError> {
    let _refresh = state
        .refresh
        .lock()
        .map_err(|error| AppError::MutexPoison(error.to_string()))?;
    remove_refresh_token()?;
    let mut auth = state
        .data
        .lock()
        .map_err(|error| AppError::MutexPoison(error.to_string()))?;
    auth.pending = None;
    auth.session = None;
    auth.generation = auth.generation.wrapping_add(1);
    let _ = sync_python_session(&app, None);
    auth.last_reason = None;
    let status = ManagedAuthStatus {
        authenticated: false,
        reason: "sign_in_required".into(),
    };
    emit_auth_changed(&app, &status);
    Ok(status)
}

fn access_token(state: &ManagedAuthState) -> Result<String, AppError> {
    {
        let auth = state
            .data
            .lock()
            .map_err(|error| AppError::MutexPoison(error.to_string()))?;
        if let Some(session) = auth
            .session
            .as_ref()
            .filter(|session| session.expires_at > now_seconds().saturating_add(30))
        {
            return Ok(session.token.clone());
        }
    }
    refresh_tokens(state)
}

#[tauri::command]
pub fn managed_entitlement(state: State<ManagedAuthState>) -> Result<ManagedEntitlement, AppError> {
    let token = access_token(&state)?;
    client::fetch_entitlement(&token)
}

fn open_managed_session(
    app: &AppHandle,
    state: &ManagedAuthState,
    path: &str,
) -> Result<(), AppError> {
    let token = access_token(state)?;
    let url = client::create_managed_session(&token, path)?;
    app.opener()
        .open_url(url.as_str(), None::<&str>)
        .map_err(|_| AppError::Other("could not open system browser".into()))
}

#[tauri::command]
pub fn managed_checkout(app: AppHandle, state: State<ManagedAuthState>) -> Result<(), AppError> {
    open_managed_session(&app, &state, "v1/billing/checkout-session")
}

#[tauri::command]
pub fn managed_billing_portal(
    app: AppHandle,
    state: State<ManagedAuthState>,
) -> Result<(), AppError> {
    open_managed_session(&app, &state, "v1/billing/portal-session")
}

#[tauri::command]
pub fn managed_delete_account(
    app: AppHandle,
    state: State<ManagedAuthState>,
) -> Result<ManagedAuthStatus, AppError> {
    let token = access_token(&state)?;
    client::request_account_deletion(&token)?;
    let _refresh = state
        .refresh
        .lock()
        .map_err(|error| AppError::MutexPoison(error.to_string()))?;
    remove_refresh_token()?;
    let mut auth = state
        .data
        .lock()
        .map_err(|error| AppError::MutexPoison(error.to_string()))?;
    auth.session = None;
    auth.pending = None;
    auth.generation = auth.generation.wrapping_add(1);
    let _ = sync_python_session(&app, None);
    auth.last_reason = Some("account_deleting".into());
    let status = ManagedAuthStatus {
        authenticated: false,
        reason: "account_deleting".into(),
    };
    emit_auth_changed(&app, &status);
    Ok(status)
}
