use poem_openapi::Object;
use serde::{Deserialize, Serialize};

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct HealthResponse {
    pub status: String,
    pub runtime: String,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct OkResponse {
    pub ok: bool,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct SettingsConflictDetail {
    pub code: String,
    pub message: String,
}

#[derive(Clone, Serialize, Deserialize, poem_openapi::Union)]
#[serde(untagged)]
#[oai(one_of)]
pub enum ErrorDetail {
    Message(String),
    Settings(SettingsConflictDetail),
    Nested(NestedErrorDetail),
}
impl From<String> for ErrorDetail {
    fn from(value: String) -> Self {
        Self::Message(value)
    }
}
impl From<&str> for ErrorDetail {
    fn from(value: &str) -> Self {
        Self::Message(value.into())
    }
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct ErrorResponse {
    pub detail: ErrorDetail,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct NestedErrorDetail {
    pub detail: String,
}
