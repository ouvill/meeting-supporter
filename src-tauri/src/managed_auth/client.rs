use std::time::Duration;

use serde::{Deserialize, Serialize};
use url::Url;

use crate::error::AppError;

const REQUEST_TIMEOUT: Duration = Duration::from_secs(15);

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

pub(super) fn validate_https_origin(raw: &str) -> Result<Url, AppError> {
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

pub(super) fn http_agent() -> ureq::Agent {
    ureq::Agent::config_builder()
        .https_only(true)
        .timeout_global(Some(REQUEST_TIMEOUT))
        .build()
        .new_agent()
}

fn managed_request(
    access_token: &str,
    method: &str,
    path: &str,
) -> Result<ureq::http::Response<ureq::Body>, AppError> {
    let base = managed_api_base_url()?;
    let url = base
        .join(path)
        .map_err(|_| AppError::Other("invalid managed service URL".into()))?;
    let authorization = format!("Bearer {access_token}");
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

pub(super) fn fetch_entitlement(access_token: &str) -> Result<ManagedEntitlement, AppError> {
    let mut response = managed_request(access_token, "GET", "v1/entitlement")?;
    response
        .body_mut()
        .read_json()
        .map_err(|_| AppError::Other("invalid managed service response".into()))
}

pub(super) fn create_managed_session(access_token: &str, path: &str) -> Result<Url, AppError> {
    let mut response = managed_request(access_token, "POST", path)?;
    let session: SessionUrl = response
        .body_mut()
        .read_json()
        .map_err(|_| AppError::Other("invalid managed service response".into()))?;
    let url =
        Url::parse(&session.url).map_err(|_| AppError::Other("invalid billing URL".into()))?;
    if url.scheme() != "https" || url.host_str().is_none() {
        return Err(AppError::Other("invalid billing URL".into()));
    }
    Ok(url)
}

pub(super) fn request_account_deletion(access_token: &str) -> Result<(), AppError> {
    managed_request(access_token, "POST", "v1/account/deletion").map(|_| ())
}
