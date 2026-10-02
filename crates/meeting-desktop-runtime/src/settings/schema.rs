//! The current Rust settings format. Defaults and validation live at this boundary.
use crate::{
    ai::routes::{Assignments, Provider},
    models::catalog::{Backend, Whisper},
    Error,
};
use meeting_media_runtime::wire::{InferenceDevice, Language};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};

pub const VERSION: u32 = 1;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Document {
    pub schema_version: u32,
    #[serde(default)]
    pub ai: Ai,
    #[serde(default)]
    pub reply: Reply,
    #[serde(default)]
    pub stt: Stt,
    #[serde(default)]
    pub audio: Audio,
    #[serde(default)]
    pub context: Context,
    #[serde(default)]
    pub usage_budget: UsageBudget,
    #[serde(default)]
    pub recording_retention: RecordingRetention,
}

impl Default for Document {
    fn default() -> Self {
        Self {
            schema_version: VERSION,
            ai: Ai::default(),
            reply: Reply::default(),
            stt: Stt::default(),
            audio: Audio::default(),
            context: Context::default(),
            usage_budget: UsageBudget::default(),
            recording_retention: RecordingRetention::default(),
        }
    }
}

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct Ai {
    pub assignments: Assignments,
    pub routes: BTreeMap<Provider, Route>,
}

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct Route {
    pub model: Option<String>,
    pub base_url: Option<String>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct Reply {
    pub enabled: bool,
    pub auto_generate: bool,
    pub default_style: String,
    pub styles: Vec<ReplyStyle>,
}

impl Default for Reply {
    fn default() -> Self {
        Self {
            enabled: true,
            auto_generate: false,
            default_style: "standard".into(),
            styles: vec![ReplyStyle {
                id: "standard".into(),
                label: "標準".into(),
                enabled: true,
                priority: 10,
                instruction: String::new(),
            }],
        }
    }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ReplyStyle {
    pub id: String,
    pub label: String,
    pub enabled: bool,
    pub priority: i64,
    #[serde(default)]
    pub instruction: String,
}

#[derive(Clone, Copy, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Vad {
    #[default]
    Silero,
}

#[derive(Clone, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct Stt {
    pub backend: Backend,
    pub whisper_model: Whisper,
    pub language: Language,
    pub device: InferenceDevice,
    pub vad_engine: Vad,
    pub vad_sensitivity: f64,
    pub silence_duration: f64,
    pub min_voiced_ms: usize,
    pub min_voiced_ratio: f64,
    pub min_rms_dbfs: f64,
}

impl Default for Stt {
    fn default() -> Self {
        Self {
            backend: Backend::Reazonspeech,
            whisper_model: Whisper::LargeV3Turbo,
            language: Language::Ja,
            device: InferenceDevice::Auto,
            vad_engine: Vad::Silero,
            vad_sensitivity: 0.4,
            silence_duration: 0.4,
            min_voiced_ms: 240,
            min_voiced_ratio: 0.35,
            min_rms_dbfs: -45.0,
        }
    }
}

#[derive(Clone, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct Audio {
    pub sample_rate: u32,
}

impl Default for Audio {
    fn default() -> Self {
        Self { sample_rate: 16000 }
    }
}

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct Context {
    pub dir_override: Option<String>,
}

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct UsageBudget {
    pub meeting_limit_jpy: f64,
    pub monthly_limit_jpy: f64,
}

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct RecordingRetention {
    pub cutoff_date: Option<String>,
    pub max_total_bytes: Option<u64>,
}

impl Document {
    pub fn parse(source: &str) -> Result<Self, Error> {
        let raw: toml::Value = toml::from_str(source).map_err(|_| Error::Settings)?;
        if raw.get("schema_version").and_then(toml::Value::as_integer) != Some(i64::from(VERSION)) {
            return Err(Error::SettingsVersion);
        }
        let document: Self = raw.try_into().map_err(|_| Error::Settings)?;
        document.validate()?;
        Ok(document)
    }

    pub fn validate(&self) -> Result<(), Error> {
        if self.schema_version != VERSION {
            return Err(Error::SettingsVersion);
        }
        let stt = &self.stt;
        if self.audio.sample_rate != 16000
            || (stt.backend == Backend::Reazonspeech && stt.language != Language::Ja)
            || !(0.05..=0.95).contains(&stt.vad_sensitivity)
            || !(0.1..=5.0).contains(&stt.silence_duration)
            || !(0.0..=1.0).contains(&stt.min_voiced_ratio)
            || !stt.min_rms_dbfs.is_finite()
            || [
                self.usage_budget.meeting_limit_jpy,
                self.usage_budget.monthly_limit_jpy,
            ]
            .iter()
            .any(|n| !n.is_finite() || *n < 0.0)
        {
            return Err(Error::Settings);
        }
        let mut ids = HashSet::new();
        if self
            .reply
            .styles
            .iter()
            .any(|style| style.id.trim().is_empty() || !ids.insert(&style.id))
            || !ids.contains(&self.reply.default_style)
            || (self.reply.enabled && !self.reply.styles.iter().any(|style| style.enabled))
        {
            return Err(Error::Settings);
        }
        if let Some(id) = &self.ai.assignments.reply {
            if !matches!(id.as_str(), "openai" | "gemini" | "anthropic" | "ollama")
                && !id
                    .strip_prefix("acp:")
                    .is_some_and(|id| !id.trim().is_empty())
            {
                return Err(Error::Settings);
            }
        }
        for route in self.ai.routes.values() {
            if route
                .model
                .as_ref()
                .is_some_and(|model| model.trim().is_empty() || model.len() > 256)
            {
                return Err(Error::Settings);
            }
            if let Some(base_url) = &route.base_url {
                let url = reqwest::Url::parse(base_url).map_err(|_| Error::Settings)?;
                if !matches!(url.scheme(), "http" | "https")
                    || url.host_str().is_none()
                    || !url.username().is_empty()
                    || url.password().is_some()
                    || url.query().is_some()
                    || url.fragment().is_some()
                {
                    return Err(Error::Settings);
                }
            }
        }
        if self
            .recording_retention
            .cutoff_date
            .as_ref()
            .is_some_and(|date| chrono::NaiveDate::parse_from_str(date, "%Y-%m-%d").is_err())
        {
            return Err(Error::Settings);
        }
        Ok(())
    }
}
