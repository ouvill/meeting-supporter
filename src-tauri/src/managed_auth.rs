use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::process::BackendState;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use jsonwebtoken::jwk::JwkSet;
use jsonwebtoken::{decode, decode_header, Algorithm, DecodingKey, Validation};
use keyring::Entry;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_opener::OpenerExt;
use url::Url;

use crate::error::AppError;

const CALLBACK_SCHEME: &str = "meeting-supporter";
const CALLBACK_HOST: &str = "oauth";
const CALLBACK_PATH: &str = "/callback";
const KEYRING_SERVICE: &str = "net.ouvill.meeting-supporter.managed";
const KEYRING_ACCOUNT: &str = "refresh-token";
const REQUEST_TIMEOUT: Duration = Duration::from_secs(15);

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
struct PendingAuthorization {
    generation: u64,
    state: String,
    nonce: String,
    verifier: String,
    token_url: Url,
    issuer: String,
    jwks_url: Url,
    client_id: String,
    redirect_url: String,
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

#[derive(Debug, Clone, Deserialize)]
struct PublicConfigEnvelope {
    auth: PublicAuthConfig,
}

#[derive(Debug, Clone, Deserialize)]
struct PublicAuthConfig {
    authorization_url: String,
    token_url: String,
    issuer: String,
    jwks_url: String,
    client_id: String,
    redirect_url: String,
    providers: Vec<String>,
    pkce_method: String,
}

#[derive(Debug, Deserialize)]
struct TokenResponse {
    access_token: String,
    refresh_token: Option<String>,
    id_token: Option<String>,
    expires_in: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct IdTokenClaims {
    nonce: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManagedEntitlement {
    pub account: ManagedAccount,
    pub plan: ManagedPlan,
    pub quota: ManagedQuota,
    pub managed: ManagedAvailability,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManagedAccount {
    pub status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManagedPlan {
    pub status: String,
    pub cancel_at_period_end: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManagedQuota {
    pub remaining_micro_usd: i64,
    pub approximate_remaining_jpy: i64,
    pub renews_at: Option<u64>,
    pub shared: bool,
    pub rollover: bool,
    pub overage_charging: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManagedAvailability {
    pub availability: String,
    pub readiness: String,
    pub reason: String,
    pub action: Option<String>,
    pub reply: ManagedCapability,
    pub speech_recognition: ManagedCapability,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManagedCapability {
    pub enabled: bool,
    pub selectable: bool,
}

#[derive(Debug, Deserialize)]
struct SessionUrl {
    url: String,
}

pub fn managed_api_base_url() -> Result<Url, AppError> {
    let raw = option_env!("MANAGED_API_BASE_URL")
        .ok_or_else(|| AppError::Other("managed service is not configured".into()))?;
    validate_https_origin(raw)
}

fn validate_https_origin(raw: &str) -> Result<Url, AppError> {
    let url = Url::parse(raw).map_err(|_| AppError::Other("invalid managed service URL".into()))?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || !matches!(url.path(), "" | "/")
    {
        return Err(AppError::Other("invalid managed service URL".into()));
    }
    Ok(url)
}

fn http_agent() -> ureq::Agent {
    ureq::Agent::config_builder()
        .https_only(true)
        .timeout_global(Some(REQUEST_TIMEOUT))
        .build()
        .new_agent()
}

fn fetch_public_auth_config(base: &Url) -> Result<PublicAuthConfig, AppError> {
    let url = base
        .join("v1/public-config")
        .map_err(|_| AppError::Other("invalid managed service URL".into()))?;
    let mut response = http_agent()
        .get(url.as_str())
        .call()
        .map_err(|_| AppError::Other("managed service is unavailable".into()))?;
    let envelope: PublicConfigEnvelope = response
        .body_mut()
        .read_json()
        .map_err(|_| AppError::Other("invalid managed service configuration".into()))?;
    validate_public_auth_config(envelope.auth)
}

fn validate_public_auth_config(config: PublicAuthConfig) -> Result<PublicAuthConfig, AppError> {
    let authorization = Url::parse(&config.authorization_url)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    let token = Url::parse(&config.token_url)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    let issuer = Url::parse(&config.issuer)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    let jwks = Url::parse(&config.jwks_url)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    if [&authorization, &token, &issuer, &jwks]
        .iter()
        .any(|url| url.scheme() != "https" || url.host_str().is_none())
        || config.redirect_url != "meeting-supporter://oauth/callback"
        || config.client_id.is_empty()
        || config.pkce_method != "S256"
        || config.providers != ["google", "microsoft"]
    {
        return Err(AppError::Other(
            "invalid authorization configuration".into(),
        ));
    }
    Ok(config)
}

fn random_urlsafe(bytes: usize) -> String {
    let mut value = vec![0_u8; bytes];
    rand::thread_rng().fill_bytes(&mut value);
    URL_SAFE_NO_PAD.encode(value)
}

fn pkce_challenge(verifier: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()))
}

fn authorization_request(
    config: PublicAuthConfig,
) -> Result<(PendingAuthorization, Url), AppError> {
    let state = random_urlsafe(32);
    let nonce = random_urlsafe(32);
    let verifier = random_urlsafe(64);
    let token_url = Url::parse(&config.token_url)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    let jwks_url = Url::parse(&config.jwks_url)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    let mut url = Url::parse(&config.authorization_url)
        .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
    url.query_pairs_mut()
        .clear()
        .append_pair("response_type", "code")
        .append_pair("client_id", &config.client_id)
        .append_pair("redirect_uri", &config.redirect_url)
        .append_pair("scope", "openid profile email offline_access")
        .append_pair("state", &state)
        .append_pair("nonce", &nonce)
        .append_pair("code_challenge", &pkce_challenge(&verifier))
        .append_pair("code_challenge_method", "S256");
    let pending = PendingAuthorization {
        generation: 0,
        state,
        nonce,
        issuer: config.issuer,
        jwks_url,
        verifier,
        token_url,
        client_id: config.client_id,
        redirect_url: config.redirect_url,
    };
    Ok((pending, url))
}

fn single_query_value(url: &Url, key: &str) -> Result<Option<String>, AppError> {
    let values = url
        .query_pairs()
        .filter_map(|(name, value)| (name == key).then(|| value.into_owned()))
        .collect::<Vec<_>>();
    if values.len() > 1 {
        return Err(AppError::Other("invalid OAuth callback".into()));
    }
    Ok(values.into_iter().next())
}

fn validate_callback(url: &Url, pending: &PendingAuthorization) -> Result<String, AppError> {
    if url.scheme() != CALLBACK_SCHEME
        || url.host_str() != Some(CALLBACK_HOST)
        || url.path() != CALLBACK_PATH
        || url.port().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err(AppError::Other("invalid OAuth callback".into()));
    }
    if single_query_value(url, "state")?.as_deref() != Some(pending.state.as_str()) {
        return Err(AppError::Other("invalid OAuth state".into()));
    }
    if single_query_value(url, "error")?.is_some() {
        return Err(AppError::Other("authorization was cancelled".into()));
    }
    single_query_value(url, "code")?
        .filter(|code| !code.is_empty())
        .ok_or_else(|| AppError::Other("authorization code missing".into()))
}

fn verify_id_token(
    issuer: &str,
    jwks_url: &Url,
    client_id: &str,
    expected_nonce: Option<&str>,
    id_token: &str,
) -> Result<(), AppError> {
    let header =
        decode_header(id_token).map_err(|_| AppError::Other("invalid identity token".into()))?;
    if header.alg != Algorithm::RS256 {
        return Err(AppError::Other("invalid identity token algorithm".into()));
    }
    let key_id = header
        .kid
        .ok_or_else(|| AppError::Other("identity token key missing".into()))?;
    let mut response = http_agent()
        .get(jwks_url.as_str())
        .call()
        .map_err(|_| AppError::Other("identity verification unavailable".into()))?;
    let jwks: JwkSet = response
        .body_mut()
        .read_json()
        .map_err(|_| AppError::Other("invalid identity key set".into()))?;
    let jwk = jwks
        .find(&key_id)
        .ok_or_else(|| AppError::Other("identity token key unknown".into()))?;
    let key =
        DecodingKey::from_jwk(jwk).map_err(|_| AppError::Other("invalid identity key".into()))?;
    let mut validation = Validation::new(Algorithm::RS256);
    validation.set_audience(&[client_id]);
    validation.set_issuer(&[issuer]);
    validation.set_required_spec_claims(&["exp", "iss", "aud"]);
    let claims = decode::<IdTokenClaims>(id_token, &key, &validation)
        .map_err(|_| AppError::Other("invalid identity token".into()))?
        .claims;
    if let Some(expected_nonce) = expected_nonce {
        if claims.nonce.as_deref() != Some(expected_nonce) {
            return Err(AppError::Other("invalid identity nonce".into()));
        }
    }
    Ok(())
}

fn exchange_authorization_code(
    pending: &PendingAuthorization,
    code: &str,
) -> Result<TokenResponse, AppError> {
    let mut response = http_agent()
        .post(pending.token_url.as_str())
        .send_form([
            ("grant_type", "authorization_code"),
            ("client_id", pending.client_id.as_str()),
            ("code", code),
            ("code_verifier", pending.verifier.as_str()),
            ("redirect_uri", pending.redirect_url.as_str()),
        ])
        .map_err(|_| AppError::Other("authorization exchange failed".into()))?;
    let tokens: TokenResponse = response
        .body_mut()
        .read_json()
        .map_err(|_| AppError::Other("invalid authorization response".into()))?;
    let id_token = tokens
        .id_token
        .as_deref()
        .ok_or_else(|| AppError::Other("identity token missing".into()))?;
    verify_id_token(
        &pending.issuer,
        &pending.jwks_url,
        &pending.client_id,
        Some(&pending.nonce),
        id_token,
    )?;
    Ok(tokens)
}

fn keyring_entry() -> Result<Entry, AppError> {
    Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT)
        .map_err(|_| AppError::Other("credential store unavailable".into()))
}

fn store_refresh_token(token: &str) -> Result<(), AppError> {
    keyring_entry()?
        .set_password(token)
        .map_err(|_| AppError::Other("credential store unavailable".into()))
}

fn read_refresh_token() -> Result<String, AppError> {
    keyring_entry()?
        .get_password()
        .map_err(|_| AppError::Other("sign in required".into()))
}

fn remove_refresh_token() -> Result<(), AppError> {
    match keyring_entry()?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(_) => Err(AppError::Other("credential store unavailable".into())),
    }
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
    let base = managed_api_base_url()?;
    let config = fetch_public_auth_config(&base)?;
    let result = http_agent().post(&config.token_url).send_form([
        ("grant_type", "refresh_token"),
        ("client_id", config.client_id.as_str()),
        ("refresh_token", refresh_token.as_str()),
    ]);
    let mut response = match result {
        Ok(response) => response,
        Err(ureq::Error::StatusCode(400 | 401)) => {
            let _ = remove_refresh_token();
            return Err(AppError::Other("sign in required".into()));
        }
        Err(_) => return Err(AppError::Other("managed authentication unavailable".into())),
    };
    let tokens: TokenResponse = response
        .body_mut()
        .read_json()
        .map_err(|_| AppError::Other("invalid authorization response".into()))?;
    if let Some(id_token) = tokens.id_token.as_deref() {
        let jwks_url = Url::parse(&config.jwks_url)
            .map_err(|_| AppError::Other("invalid authorization configuration".into()))?;
        verify_id_token(&config.issuer, &jwks_url, &config.client_id, None, id_token)?;
    }
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
        match validate_callback(&url, pending) {
            Ok(code) => auth.pending.take().map(|pending| (pending, code)),
            Err(_) => None,
        }
    };
    let Some((pending, code)) = pending_and_code else {
        return;
    };
    std::thread::spawn(move || {
        let result = exchange_authorization_code(&pending, &code).and_then(|tokens| {
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
    let base = managed_api_base_url()?;
    let config = fetch_public_auth_config(&base)?;
    let (mut pending, authorization_url) = authorization_request(config)?;
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

fn managed_request(
    state: &ManagedAuthState,
    method: &str,
    path: &str,
) -> Result<ureq::http::Response<ureq::Body>, AppError> {
    let base = managed_api_base_url()?;
    let url = base
        .join(path)
        .map_err(|_| AppError::Other("invalid managed service URL".into()))?;
    let token = access_token(state)?;
    let authorization = format!("Bearer {token}");
    let agent = http_agent();
    let result = match method {
        "GET" => agent
            .get(url.as_str())
            .header("authorization", &authorization)
            .call(),
        "POST" => agent
            .post(url.as_str())
            .header("authorization", &authorization)
            .send_empty(),
        _ => return Err(AppError::Other("invalid managed request method".into())),
    };
    result.map_err(|_| AppError::Other("managed service request failed".into()))
}

#[tauri::command]
pub fn managed_entitlement(state: State<ManagedAuthState>) -> Result<ManagedEntitlement, AppError> {
    let mut response = managed_request(&state, "GET", "v1/entitlement")?;
    response
        .body_mut()
        .read_json()
        .map_err(|_| AppError::Other("invalid managed service response".into()))
}

fn open_managed_session(
    app: &AppHandle,
    state: &ManagedAuthState,
    path: &str,
) -> Result<(), AppError> {
    let mut response = managed_request(state, "POST", path)?;
    let session: SessionUrl = response
        .body_mut()
        .read_json()
        .map_err(|_| AppError::Other("invalid managed service response".into()))?;
    let url =
        Url::parse(&session.url).map_err(|_| AppError::Other("invalid billing URL".into()))?;
    if url.scheme() != "https" || url.host_str().is_none() {
        return Err(AppError::Other("invalid billing URL".into()));
    }
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
    let _ = managed_request(&state, "POST", "v1/account/deletion")?;
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

#[cfg(test)]
mod tests {
    use super::*;

    fn pending() -> PendingAuthorization {
        PendingAuthorization {
            generation: 1,
            state: "expected-state".into(),
            nonce: "expected-nonce".into(),
            verifier: "verifier".into(),
            token_url: Url::parse("https://clerk.example/oauth/token").unwrap(),
            issuer: "https://clerk.example".into(),
            jwks_url: Url::parse("https://clerk.example/.well-known/jwks.json").unwrap(),
            client_id: "client".into(),
            redirect_url: "meeting-supporter://oauth/callback".into(),
        }
    }

    #[test]
    fn callback_requires_exact_scheme_host_path_and_state() {
        let valid =
            Url::parse("meeting-supporter://oauth/callback?code=abc&state=expected-state").unwrap();
        assert_eq!(validate_callback(&valid, &pending()).unwrap(), "abc");
        for invalid in [
            "meeting-supporter://evil/callback?code=abc&state=expected-state",
            "meeting-supporter://oauth/other?code=abc&state=expected-state",
            "meeting-supporter://oauth/callback?code=abc&state=wrong",
            "meeting-supporter://oauth/callback?code=abc&code=def&state=expected-state",
            "meeting-supporter://oauth/callback?code=abc&state=expected-state#fragment",
        ] {
            assert!(validate_callback(&Url::parse(invalid).unwrap(), &pending()).is_err());
        }
    }

    #[test]
    fn pkce_is_s256_urlsafe_without_padding() {
        assert_eq!(
            pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        );
    }

    #[test]
    fn managed_origin_must_be_an_https_origin() {
        assert!(validate_https_origin("https://managed.example").is_ok());
        for invalid in [
            "http://managed.example",
            "https://user@managed.example",
            "https://managed.example/path",
            "https://managed.example?query=1",
        ] {
            assert!(validate_https_origin(invalid).is_err());
        }
    }
}
