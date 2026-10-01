use poem_openapi::{Enum, Object};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum SpeechBackend {
    #[serde(rename = "whisper")]
    #[oai(rename = "whisper")]
    Whisper,
    #[serde(rename = "reazonspeech")]
    #[oai(rename = "reazonspeech")]
    Reazonspeech,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum VadEngine {
    #[serde(rename = "silero")]
    #[oai(rename = "silero")]
    Silero,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum ModelLanguage {
    #[serde(rename = "ja")]
    #[oai(rename = "ja")]
    Ja,
    #[serde(rename = "en")]
    #[oai(rename = "en")]
    En,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum WhisperModel {
    #[serde(rename = "tiny")]
    #[oai(rename = "tiny")]
    Tiny,
    #[serde(rename = "base")]
    #[oai(rename = "base")]
    Base,
    #[serde(rename = "small")]
    #[oai(rename = "small")]
    Small,
    #[serde(rename = "medium")]
    #[oai(rename = "medium")]
    Medium,
    #[serde(rename = "large-v2")]
    #[oai(rename = "large-v2")]
    LargeV2,
    #[serde(rename = "large-v3-turbo")]
    #[oai(rename = "large-v3-turbo")]
    LargeV3Turbo,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum ModelState {
    #[serde(rename = "missing")]
    #[oai(rename = "missing")]
    Missing,
    #[serde(rename = "downloading")]
    #[oai(rename = "downloading")]
    Downloading,
    #[serde(rename = "ready")]
    #[oai(rename = "ready")]
    Ready,
    #[serde(rename = "failed")]
    #[oai(rename = "failed")]
    Failed,
    #[serde(rename = "cancelled")]
    #[oai(rename = "cancelled")]
    Cancelled,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum ModelPhase {
    #[serde(rename = "idle")]
    #[oai(rename = "idle")]
    Idle,
    #[serde(rename = "downloading")]
    #[oai(rename = "downloading")]
    Downloading,
    #[serde(rename = "verifying")]
    #[oai(rename = "verifying")]
    Verifying,
    #[serde(rename = "extracting")]
    #[oai(rename = "extracting")]
    Extracting,
    #[serde(rename = "ready")]
    #[oai(rename = "ready")]
    Ready,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum ModelErrorCode {
    #[serde(rename = "network")]
    #[oai(rename = "network")]
    Network,
    #[serde(rename = "disk_full")]
    #[oai(rename = "disk_full")]
    DiskFull,
    #[serde(rename = "permission")]
    #[oai(rename = "permission")]
    Permission,
    #[serde(rename = "checksum")]
    #[oai(rename = "checksum")]
    Checksum,
    #[serde(rename = "archive")]
    #[oai(rename = "archive")]
    Archive,
    #[serde(rename = "cancelled")]
    #[oai(rename = "cancelled")]
    Cancelled,
    #[serde(rename = "unknown")]
    #[oai(rename = "unknown")]
    Unknown,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct SpeechModelDownloadRequest {
    pub backend: SpeechBackend,
    pub language: ModelLanguage,
    #[oai(nullable)]
    pub model: Option<WhisperModel>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct SpeechModelStatusResponse {
    pub backend: SpeechBackend,
    pub model_id: String,
    pub state: ModelState,
    pub phase: ModelPhase,
    pub language: ModelLanguage,
    pub downloaded_bytes: i64,
    #[oai(nullable)]
    pub total_bytes: Option<i64>,
    #[oai(nullable)]
    pub progress_percent: Option<i64>,
    #[oai(nullable)]
    pub model_path: Option<String>,
    pub storage_path: String,
    #[oai(nullable)]
    pub error_code: Option<ModelErrorCode>,
    pub message: String,
    pub retryable: bool,
    pub cancelable: bool,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct SpeechCapabilities {
    #[oai(nullable)]
    pub whisper_gpu: Option<bool>,
}
