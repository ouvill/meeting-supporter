use super::*;
use poem_openapi::{types::MaybeUndefined, Enum, Object};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum SecretKey {
    #[serde(rename = "GEMINI_API_KEY")]
    #[oai(rename = "GEMINI_API_KEY")]
    GeminiApiKey,
    #[serde(rename = "OPENAI_API_KEY")]
    #[oai(rename = "OPENAI_API_KEY")]
    OpenaiApiKey,
    #[serde(rename = "ANTHROPIC_API_KEY")]
    #[oai(rename = "ANTHROPIC_API_KEY")]
    AnthropicApiKey,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct OllamaConfig {
    pub base_url: String,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct SttSettings {
    pub backend: SpeechBackend,
    pub whisper_model: String,
    pub language: String,
    pub vad_engine: VadEngine,
    pub vad_sensitivity: f64,
    pub silence_duration: f64,
    pub device: String,
    pub min_voiced_ms: i64,
    pub min_voiced_ratio: f64,
    pub min_rms_dbfs: f64,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct AudioSettings {
    pub sample_rate: i64,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct ReplyStyleSettings {
    pub id: String,
    pub label: String,
    pub enabled: bool,
    pub priority: i64,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct ReplySettings {
    pub enabled: bool,
    pub auto_generate: bool,
    pub default_style: String,
    pub styles: Vec<ReplyStyleSettings>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct SecretsStatus {
    #[serde(rename = "GEMINI_API_KEY")]
    #[oai(rename = "GEMINI_API_KEY")]
    pub gemini_api_key: bool,
    #[serde(rename = "OPENAI_API_KEY")]
    #[oai(rename = "OPENAI_API_KEY")]
    pub openai_api_key: bool,
    #[serde(rename = "ANTHROPIC_API_KEY")]
    #[oai(rename = "ANTHROPIC_API_KEY")]
    pub anthropic_api_key: bool,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct ProviderSummary {
    pub id: String,
    pub label: String,
    pub kind: ProviderKind,
    pub data_location: DataLocation,
    #[oai(nullable)]
    pub base_url: Option<String>,
    #[oai(nullable)]
    pub models: Option<Vec<String>>,
    pub experimental: bool,
    #[oai(nullable)]
    pub api_key_configured: Option<bool>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct UsageBudgetConfig {
    pub meeting_limit_jpy: f64,
    pub monthly_limit_jpy: f64,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct UsageSummaryResponse {
    #[serde(default)]
    #[oai(default)]
    pub input_tokens: i64,
    #[serde(default)]
    #[oai(default)]
    pub output_tokens: i64,
    #[serde(default)]
    #[oai(default)]
    pub estimated_cost_jpy: f64,
    #[serde(default)]
    #[oai(default)]
    pub request_count: i64,
    #[serde(default)]
    #[oai(default)]
    pub incomplete_requests: i64,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct UsageSettingsResponse {
    pub budget: UsageBudgetConfig,
    pub current_meeting: UsageSummaryResponse,
    pub current_month: UsageSummaryResponse,
    pub billing_mode: String,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct RecordingRetentionSettings {
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub cutoff_date: MaybeUndefined<String>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub max_total_bytes: MaybeUndefined<i64>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct SettingsResponse {
    pub ollama: OllamaConfig,
    pub stt: SttSettings,
    pub audio: AudioSettings,
    pub reply: ReplySettings,
    pub secrets: SecretsStatus,
    pub data_dir: String,
    pub context_dir: String,
    pub providers: Vec<ProviderSummary>,
    pub usage: UsageSettingsResponse,
    pub recording_retention: RecordingRetentionSettings,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct OllamaConfigPayload {
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub base_url: MaybeUndefined<String>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct ReplyStyleEnabledPatch {
    pub id: String,
    pub enabled: bool,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct ReplySettingsPayload {
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub enabled: MaybeUndefined<bool>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub auto_generate: MaybeUndefined<bool>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub default_style: MaybeUndefined<String>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub styles: MaybeUndefined<Vec<ReplyStyleEnabledPatch>>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct SecretsPayload {
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    #[serde(rename = "GEMINI_API_KEY")]
    #[oai(rename = "GEMINI_API_KEY")]
    pub gemini_api_key: MaybeUndefined<String>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    #[serde(rename = "OPENAI_API_KEY")]
    #[oai(rename = "OPENAI_API_KEY")]
    pub openai_api_key: MaybeUndefined<String>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    #[serde(rename = "ANTHROPIC_API_KEY")]
    #[oai(rename = "ANTHROPIC_API_KEY")]
    pub anthropic_api_key: MaybeUndefined<String>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct SttSettingsPatch {
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub backend: MaybeUndefined<String>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub whisper_model: MaybeUndefined<String>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub language: MaybeUndefined<String>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub vad_engine: MaybeUndefined<String>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub vad_sensitivity: MaybeUndefined<f64>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub silence_duration: MaybeUndefined<f64>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub device: MaybeUndefined<String>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub min_voiced_ms: MaybeUndefined<i64>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub min_voiced_ratio: MaybeUndefined<f64>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub min_rms_dbfs: MaybeUndefined<f64>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct AudioSettingsPatch {
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub sample_rate: MaybeUndefined<i64>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct ContextSettingsPatch {
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub dir_override: MaybeUndefined<String>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct UsageBudgetPatch {
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub meeting_limit_jpy: MaybeUndefined<f64>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub monthly_limit_jpy: MaybeUndefined<f64>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct SettingsSaveRequest {
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub ollama: MaybeUndefined<OllamaConfigPayload>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub reply: MaybeUndefined<ReplySettingsPayload>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub secrets: MaybeUndefined<SecretsPayload>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub stt: MaybeUndefined<SttSettingsPatch>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub audio: MaybeUndefined<AudioSettingsPatch>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub context: MaybeUndefined<ContextSettingsPatch>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub usage_budget: MaybeUndefined<UsageBudgetPatch>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub recording_retention: MaybeUndefined<RecordingRetentionSettings>,
    #[serde(default, skip_serializing_if = "MaybeUndefined::is_undefined")]
    #[oai(nullable)]
    pub delete_secrets: MaybeUndefined<Vec<SecretKey>>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct SaveSettingsResponse {
    pub ok: bool,
    pub settings: SettingsResponse,
}
