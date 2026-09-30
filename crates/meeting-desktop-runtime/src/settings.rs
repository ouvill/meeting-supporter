//! Compatible settings persistence. Secrets never enter TOML or response payloads.
use crate::{Config, Error};
use meeting_media_runtime::wire::SpeechConfig;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::{
    collections::BTreeMap,
    io::Write,
    path::{Path, PathBuf},
    sync::Arc,
};

const DEFAULTS: &str = include_str!("../../../python/config.default.toml");
pub const SECRET_KEYS: [&str; 7] = [
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "XAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "DEEPGRAM_API_KEY",
];
/// Implemented by the desktop OS credential adapter; tests use an isolated memory store.
pub trait Secrets: Send + Sync {
    fn get(&self, key: &str) -> Result<Option<String>, Error>;
    fn set(&self, key: &str, value: Option<&str>) -> Result<(), Error>;
}
pub struct UnavailableSecrets;
impl Secrets for UnavailableSecrets {
    fn get(&self, _: &str) -> Result<Option<String>, Error> {
        Ok(None)
    }
    fn set(&self, _: &str, _: Option<&str>) -> Result<(), Error> {
        Err(Error::Secrets)
    }
}
#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct Patch {
    pub stt: Option<Map<String, Value>>,
    pub audio: Option<Map<String, Value>>,
    pub reply: Option<Map<String, Value>>,
    pub agents: Option<Map<String, Value>>,
    pub ollama: Option<Map<String, Value>>,
    pub acp: Option<Map<String, Value>>,
    pub context: Option<Map<String, Value>>,
    pub usage_budget: Option<Map<String, Value>>,
    pub recording_retention: Option<Map<String, Value>>,
    pub secrets: Option<BTreeMap<String, Option<String>>>,
    pub delete_secrets: Option<Vec<String>>,
}
#[derive(Deserialize, Serialize)]
struct ReplyStyle {
    id: String,
    #[serde(default)]
    label: String,
    #[serde(default = "default_style_enabled")]
    enabled: bool,
    #[serde(default = "default_style_priority")]
    priority: i64,
}
fn default_style_enabled() -> bool {
    true
}
fn default_style_priority() -> i64 {
    100
}
#[derive(Clone)]
pub struct Store {
    directory: PathBuf,
    pub document: Value,
    secrets: Arc<dyn Secrets>,
}
fn invalid() -> Error {
    Error::Settings
}
fn parse(source: &str) -> Result<Value, Error> {
    let value: toml::Value = toml::from_str(source).map_err(|_| invalid())?;
    serde_json::to_value(value).map_err(|_| invalid())
}
fn merge(base: &mut Value, patch: &Value) {
    if let (Some(base), Some(patch)) = (base.as_object_mut(), patch.as_object()) {
        for (key, value) in patch {
            match base.get_mut(key) {
                Some(current) if current.is_object() && value.is_object() => merge(current, value),
                _ => {
                    base.insert(key.clone(), value.clone());
                }
            }
        }
    }
}
impl Store {
    pub(crate) fn secret(&self, key: &str) -> Result<Option<String>, Error> {
        self.secrets.get(key)
    }

    pub fn open(config: &Config, secrets: Arc<dyn Secrets>) -> Result<Self, Error> {
        let mut document = parse(DEFAULTS)?;
        match std::fs::read_to_string(config.data_dir.join("config.toml")) {
            Ok(text) => merge(&mut document, &parse(&text)?),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        validate_document(&document)?;
        Ok(Self {
            directory: config.data_dir.clone(),
            document,
            secrets,
        })
    }
    pub fn speech(&self) -> Result<SpeechConfig, Error> {
        let stt = &self.document["stt"];
        if (stt["backend"] != "reazonspeech" && stt["backend"] != "whisper")
            || stt["vad_engine"] != "silero"
            || (stt["backend"] == "reazonspeech" && stt["language"] != "ja")
            || self.document["audio"]["sample_rate"] != 16000
        {
            return Err(Error::Unsupported);
        }
        self.recognizer()?;
        if stt["backend"] == "whisper" {
            self.whisper_model()?;
        }
        let defaults = parse(DEFAULTS)?;
        let known = stt
            .as_object()
            .ok_or_else(invalid)?
            .iter()
            .filter(|(key, _)| defaults["stt"].get(*key).is_some())
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        validate_section("stt", &known)?;
        Ok(SpeechConfig {
            vad_threshold: stt["vad_sensitivity"].as_f64().ok_or_else(invalid)? as f32,
            silence_seconds: stt["silence_duration"].as_f64().ok_or_else(invalid)?,
            min_voiced_ms: usize::try_from(stt["min_voiced_ms"].as_u64().ok_or_else(invalid)?)
                .map_err(|_| invalid())?,
            min_voiced_ratio: stt["min_voiced_ratio"].as_f64().ok_or_else(invalid)?,
            min_rms_dbfs: stt["min_rms_dbfs"].as_f64().ok_or_else(invalid)?,
        })
    }
    pub(crate) fn recognizer(&self) -> Result<meeting_media_runtime::wire::Recognizer, Error> {
        use meeting_media_runtime::wire::{InferenceDevice, Language, Recognizer};
        match self.backend().as_str() {
            "reazonspeech" => Ok(Recognizer::Reazonspeech),
            "whisper" => {
                let device = match self.document["stt"]["device"].as_str() {
                    Some("auto") => InferenceDevice::Auto,
                    Some("cpu") => InferenceDevice::Cpu,
                    Some("gpu") => InferenceDevice::Gpu,
                    // CUDA is a legacy Python setting, not a promise of Vulkan availability.
                    _ => return Err(Error::Settings),
                };
                let language: Language =
                    serde_json::from_value(self.document["stt"]["language"].clone())
                        .map_err(|_| Error::Settings)?;
                Ok(Recognizer::Whisper { device, language })
            }
            _ => Err(Error::Unsupported),
        }
    }
    pub(crate) fn whisper_model(&self) -> Result<crate::models::catalog::Whisper, Error> {
        serde_json::from_value(self.document["stt"]["whisper_model"].clone())
            .map_err(|_| Error::Settings)
    }
    pub fn backend(&self) -> String {
        self.document["stt"]["backend"]
            .as_str()
            .unwrap_or("reazonspeech")
            .into()
    }
    pub(crate) fn agent_event(&self) -> Result<crate::wire::Event, Error> {
        let styles: Vec<ReplyStyle> =
            serde_json::from_value(self.document["reply"]["styles"].clone())
                .map_err(|_| invalid())?;
        Ok(crate::wire::Event::AgentSettings {
            reply_enabled: self.document["reply"]["enabled"]
                .as_bool()
                .ok_or_else(invalid)?,
            reply_auto_generate: self.document["reply"]["auto_generate"]
                .as_bool()
                .ok_or_else(invalid)?,
            info_enabled: self.document["agents"]["info_enabled"]
                .as_bool()
                .ok_or_else(invalid)?,
            reply_agents: styles
                .into_iter()
                .map(|style| crate::wire::ReplyAgent {
                    id: style.id,
                    label: style.label,
                    enabled: style.enabled,
                    priority: style.priority,
                })
                .collect(),
        })
    }
    pub fn response(&self) -> Result<Value, Error> {
        let mut secrets = Map::new();
        for key in SECRET_KEYS {
            secrets.insert(
                key.into(),
                json!(self.secrets.get(key)?.is_some_and(|v| !v.is_empty())),
            );
        }
        let document = &self.document;
        let styles: Vec<ReplyStyle> =
            serde_json::from_value(document["reply"]["styles"].clone()).map_err(|_| invalid())?;
        let context = document["context"]["dir_override"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| self.directory.join("context"));
        let defaults = parse(DEFAULTS)?;
        let stt: Map<String, Value> = document["stt"]
            .as_object()
            .ok_or_else(invalid)?
            .iter()
            .filter(|(key, _)| defaults["stt"].get(*key).is_some())
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        Ok(json!({
            "stt":stt,"audio":{"sample_rate":document["audio"]["sample_rate"],"max_session_seconds":document["audio"]["max_session_seconds"]},"ollama":{"base_url":document["ollama"]["base_url"]},
            "acp":{"command":document.pointer("/ai/routes/acp/command").cloned().unwrap_or(json!([])),"runtime":"acp","capabilities":["reply"]},
            "reply":{"enabled":document["reply"]["enabled"],"auto_generate":document["reply"]["auto_generate"],"default_style":document["reply"]["default_style"],"styles":styles},
            "agents":{"info_enabled":document["agents"]["info_enabled"]},
            "secrets":secrets,"providers":[],"data_dir":self.directory,"context_dir":context,
            "usage":{"budget":{"meeting_limit_jpy":document["usage_budget"]["meeting_limit_jpy"],"monthly_limit_jpy":document["usage_budget"]["monthly_limit_jpy"]},"current_meeting":{"input_tokens":0,"output_tokens":0,"estimated_cost_jpy":0.0,"request_count":0},"current_month":crate::usage::month(&self.directory.join("usage.jsonl"))?,"billing_mode": match self.document.pointer("/ai/assignments/reply").and_then(Value::as_str) { Some("ollama")=>"local", Some("openai"|"gemini"|"anthropic")=>"byok", _=>"unassigned" }},
            "recording_retention":{"cutoff_date":document["recording_retention"]["cutoff_date"],"max_total_bytes":document["recording_retention"]["max_total_bytes"].as_u64().filter(|v| *v>0)}
        }))
    }
    pub fn candidate(&self, patch: &Patch) -> Result<Value, Error> {
        let mut document = self.document.clone();
        for (name, section) in [
            ("stt", &patch.stt),
            ("audio", &patch.audio),
            ("agents", &patch.agents),
            ("ollama", &patch.ollama),
            ("context", &patch.context),
            ("usage_budget", &patch.usage_budget),
            ("recording_retention", &patch.recording_retention),
        ] {
            if let Some(section) = section {
                validate_section(name, section)?;
                for (key, value) in section {
                    if !value.is_null() {
                        document[name][key] = value.clone();
                    } else if name == "recording_retention" {
                        document[name]
                            .as_object_mut()
                            .ok_or_else(invalid)?
                            .remove(key);
                    }
                }
            }
        }
        if let Some(reply) = &patch.reply {
            for (key, value) in reply {
                if value.is_null() {
                    continue;
                }
                match key.as_str() {
                    "enabled" | "auto_generate" if value.is_boolean() => {
                        document["reply"][key] = value.clone()
                    }
                    "default_style" if value.is_string() => document["reply"][key] = value.clone(),
                    "styles" => {
                        #[derive(Deserialize)]
                        #[serde(deny_unknown_fields)]
                        struct Style {
                            id: String,
                            enabled: bool,
                        }
                        let styles: Vec<Style> =
                            serde_json::from_value(value.clone()).map_err(|_| invalid())?;
                        let configured = document["reply"]["styles"]
                            .as_array_mut()
                            .ok_or_else(invalid)?;
                        let mut seen = std::collections::HashSet::new();
                        for style in styles {
                            if !seen.insert(style.id.clone()) {
                                return Err(invalid());
                            }
                            let target = configured
                                .iter_mut()
                                .find(|v| v["id"] == style.id)
                                .ok_or_else(invalid)?;
                            target["enabled"] = json!(style.enabled);
                        }
                    }
                    _ => return Err(invalid()),
                }
            }
            let styles = document["reply"]["styles"].as_array().ok_or_else(invalid)?;
            if !styles
                .iter()
                .any(|s| s["id"] == document["reply"]["default_style"])
            {
                return Err(invalid());
            }
            if document["reply"]["enabled"] == true
                && !styles
                    .iter()
                    .any(|s| s["enabled"].as_bool().unwrap_or(true))
            {
                return Err(invalid());
            }
        }
        if let Some(acp) = &patch.acp {
            for (key, value) in acp {
                if key != "command" {
                    return Err(invalid());
                }
                if value.is_null() {
                    continue;
                }
                let args: Vec<String> =
                    serde_json::from_value(value.clone()).map_err(|_| invalid())?;
                if args.len() > 32 || args.iter().any(|s| s.trim().is_empty()) {
                    return Err(invalid());
                }
                document["ai"]["routes"]["acp"]["command"] = json!(args);
            }
        }
        for key in patch
            .secrets
            .iter()
            .flat_map(|p| p.keys())
            .chain(patch.delete_secrets.iter().flatten())
        {
            if !SECRET_KEYS.contains(&key.as_str()) {
                return Err(invalid());
            }
        }
        validate_document(&document)?;
        Ok(document)
    }
    pub fn audio_changed(&self, candidate: &Value) -> bool {
        self.document["stt"] != candidate["stt"] || self.document["audio"] != candidate["audio"]
    }
    pub fn save(&mut self, candidate: Value, patch: Patch) -> Result<(), Error> {
        let mut changes = BTreeMap::<String, Option<String>>::new();
        for (key, value) in patch.secrets.unwrap_or_default() {
            if let Some(value) = value.filter(|s| !s.trim().is_empty()) {
                changes.insert(key, Some(value));
            }
        }
        for key in patch.delete_secrets.unwrap_or_default() {
            if changes.insert(key, None).is_some() {
                return Err(invalid());
            }
        }
        let mut before = BTreeMap::new();
        for key in changes.keys() {
            before.insert(key.clone(), self.secrets.get(key)?);
        }
        // Build the file first; no credentials have changed if validation/serialization fails.
        let text = toml::to_string_pretty(&candidate).map_err(|_| invalid())?;
        let result = (|| {
            for (key, value) in &changes {
                self.secrets.set(key, value.as_deref())?;
            }
            atomic_write(&self.directory.join("config.toml"), text.as_bytes())
        })();
        if let Err(error) = result {
            let mut restored = true;
            for (key, value) in &before {
                if self.secrets.set(key, value.as_deref()).is_err() {
                    restored = false;
                }
            }
            return Err(if restored {
                error
            } else {
                Error::SecretsRollback
            });
        }
        self.document = candidate;
        Ok(())
    }
}
fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), Error> {
    let mut file = tempfile::NamedTempFile::new_in(path.parent().ok_or_else(invalid)?)?;
    // NamedTempFile creates mode 0600 on Unix. The original file survives failed writes.
    file.write_all(bytes)?;
    file.as_file().sync_all()?;
    file.persist(path).map_err(|error| Error::Io(error.error))?;
    Ok(())
}
fn validate_document(document: &Value) -> Result<(), Error> {
    let defaults = parse(DEFAULTS)?;
    for name in [
        "stt",
        "audio",
        "agents",
        "ollama",
        "usage_budget",
        "context",
        "recording_retention",
    ] {
        let section = document[name].as_object().ok_or_else(invalid)?;
        let known = section
            .iter()
            .filter(|(key, _)| {
                defaults[name].get(*key).is_some()
                    || matches!(
                        (name, key.as_str()),
                        ("context", "dir_override") | ("recording_retention", "cutoff_date")
                    )
            })
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        validate_section(name, &known)?;
    }
    for key in ["enabled", "auto_generate"] {
        if !document["reply"][key].is_boolean() {
            return Err(invalid());
        }
    }
    if !document["reply"]["default_style"].is_string() {
        return Err(invalid());
    }
    let styles: Vec<ReplyStyle> =
        serde_json::from_value(document["reply"]["styles"].clone()).map_err(|_| invalid())?;
    let mut ids = std::collections::HashSet::new();
    if styles.is_empty()
        || styles
            .iter()
            .any(|style| style.id.is_empty() || !ids.insert(&style.id))
    {
        return Err(invalid());
    }
    if let Some(command) = document.pointer("/ai/routes/acp/command") {
        let args: Vec<String> = serde_json::from_value(command.clone()).map_err(|_| invalid())?;
        if args.len() > 32 || args.iter().any(|s| s.trim().is_empty()) {
            return Err(invalid());
        }
    }
    Ok(())
}
fn validate_section(name: &str, section: &Map<String, Value>) -> Result<(), Error> {
    let defaults = parse(DEFAULTS)?;
    for (key, value) in section {
        let reference = defaults[name].get(key);
        let allowed = reference.is_some()
            || matches!(
                (name, key.as_str()),
                ("context", "dir_override") | ("recording_retention", "cutoff_date")
            );
        if !allowed {
            return Err(invalid());
        }
        if value.is_null() {
            continue;
        }
        if let Some(reference) = reference {
            let valid = match reference {
                Value::Bool(_) => value.is_boolean(),
                Value::String(_) => value.is_string(),
                Value::Number(n) if n.is_i64() => value.as_i64().is_some(),
                Value::Number(_) => value.as_f64().is_some_and(f64::is_finite),
                Value::Array(_) => value
                    .as_array()
                    .is_some_and(|a| a.iter().all(Value::is_string)),
                _ => false,
            };
            if !valid {
                return Err(invalid());
            }
        } else if !value.is_string() {
            return Err(invalid());
        }
        if let Some(n) = value.as_f64() {
            let bounds = match key.as_str() {
                "vad_sensitivity" => (0.05, 0.95),
                "silence_duration" => (0.1, 5.0),
                "vad_aggressiveness" => (0.0, 3.0),
                "sample_rate" => (1.0, 192000.0),
                "max_session_seconds" => (1.0, 60.0),
                key if key.ends_with("ratio") && !key.contains("compression")
                    || key.ends_with("no_speech_threshold")
                    || key == "drop_score_threshold" =>
                {
                    (0.0, 1.0)
                }
                key if key.contains("compression_ratio") => (f64::MIN_POSITIVE, f64::MAX),
                key if key.ends_with("_ms")
                    || key.ends_with("_jpy")
                    || key == "max_total_bytes"
                    || key == "temperature" =>
                {
                    (0.0, f64::MAX)
                }
                _ => (-f64::MAX, f64::MAX),
            };
            if !n.is_finite() || n < bounds.0 || n > bounds.1 {
                return Err(invalid());
            }
        }
        if name == "stt" && key == "vad_engine" && value != "silero" && value != "webrtc" {
            return Err(invalid());
        }
        if name == "recording_retention"
            && key == "cutoff_date"
            && chrono::NaiveDate::parse_from_str(value.as_str().ok_or_else(invalid)?, "%Y-%m-%d")
                .is_err()
        {
            return Err(invalid());
        }
    }
    Ok(())
}

/// Existing secrets.toml compatibility, with private atomic writes.
pub struct FileSecrets {
    pub path: PathBuf,
}
impl FileSecrets {
    fn load(&self) -> Result<BTreeMap<String, String>, Error> {
        match std::fs::read_to_string(&self.path) {
            Ok(text) => toml::from_str(&text).map_err(|_| Error::Secrets),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(BTreeMap::new()),
            Err(_) => Err(Error::Secrets),
        }
    }
}
impl Secrets for FileSecrets {
    fn get(&self, key: &str) -> Result<Option<String>, Error> {
        Ok(self.load()?.remove(key))
    }
    fn set(&self, key: &str, value: Option<&str>) -> Result<(), Error> {
        let mut values = self.load()?;
        match value {
            Some(value) => {
                values.insert(key.into(), value.into());
            }
            None => {
                values.remove(key);
            }
        }
        atomic_write(
            &self.path,
            toml::to_string(&values)
                .map_err(|_| Error::Secrets)?
                .as_bytes(),
        )
    }
}
/// Read legacy credentials, but do not silently switch new writes to plaintext.
pub struct MigratingSecrets {
    pub primary: Arc<dyn Secrets>,
    pub legacy: FileSecrets,
}
impl Secrets for MigratingSecrets {
    fn get(&self, key: &str) -> Result<Option<String>, Error> {
        match self.primary.get(key) {
            Ok(Some(value)) => Ok(Some(value)),
            _ => self.legacy.get(key),
        }
    }
    fn set(&self, key: &str, value: Option<&str>) -> Result<(), Error> {
        let previous = self.primary.get(key)?;
        self.primary.set(key, value)?;
        let cleanup = (|| {
            if self.legacy.get(key)?.is_some() {
                self.legacy.set(key, None)?;
            }
            Ok::<_, Error>(())
        })();
        if let Err(error) = cleanup {
            self.primary
                .set(key, previous.as_deref())
                .map_err(|_| Error::SecretsRollback)?;
            return Err(error);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;
    #[derive(Default)]
    struct MemorySecrets {
        values: Mutex<BTreeMap<String, String>>,
    }
    impl Secrets for MemorySecrets {
        fn get(&self, key: &str) -> Result<Option<String>, Error> {
            Ok(self.values.lock().unwrap().get(key).cloned())
        }
        fn set(&self, key: &str, value: Option<&str>) -> Result<(), Error> {
            if value == Some("synthetic-failure") {
                return Err(Error::Secrets);
            }
            let mut values = self.values.lock().unwrap();
            if let Some(value) = value {
                values.insert(key.into(), value.into());
            } else {
                values.remove(key);
            }
            Ok(())
        }
    }
    fn store(dir: &Path, secrets: Arc<dyn Secrets>) -> Store {
        Store {
            directory: dir.into(),
            document: parse(DEFAULTS).unwrap(),
            secrets,
        }
    }
    fn patch(value: Value) -> Patch {
        serde_json::from_value(value).unwrap()
    }
    #[test]
    fn credentials_are_redacted_and_partial_failure_rolls_back() {
        let temp = tempfile::tempdir().unwrap();
        let credentials = Arc::new(MemorySecrets::default());
        let mut store = store(temp.path(), credentials.clone());
        credentials
            .set("GEMINI_API_KEY", Some("synthetic-original"))
            .unwrap();
        let change = patch(
            json!({"secrets":{"GEMINI_API_KEY":"synthetic-new","XAI_API_KEY":"synthetic-failure"},"stt":{"silence_duration":0.8}}),
        );
        let candidate = store.candidate(&change).unwrap();
        assert!(store.save(candidate, change).is_err());
        assert_eq!(
            credentials.get("GEMINI_API_KEY").unwrap().as_deref(),
            Some("synthetic-original")
        );
        assert!(!temp.path().join("config.toml").exists());
        assert_eq!(store.document["stt"]["silence_duration"], 0.4);
        let change = patch(json!({"secrets":{"GEMINI_API_KEY":"synthetic-new"}}));
        store
            .save(store.candidate(&change).unwrap(), change)
            .unwrap();
        let response = store.response().unwrap();
        assert_eq!(response["secrets"]["GEMINI_API_KEY"], true);
        assert!(!response.to_string().contains("synthetic-new"));
        assert!(!std::fs::read_to_string(temp.path().join("config.toml"))
            .unwrap()
            .contains("synthetic-new"));
        let change = patch(json!({"delete_secrets":["GEMINI_API_KEY"]}));
        store
            .save(store.candidate(&change).unwrap(), change)
            .unwrap();
        assert_eq!(credentials.get("GEMINI_API_KEY").unwrap(), None);
    }
    #[test]
    fn failed_file_replace_restores_secrets_and_preserves_in_memory_configuration() {
        let temp = tempfile::tempdir().unwrap();
        let credentials = Arc::new(MemorySecrets::default());
        let mut store = store(temp.path(), credentials.clone());
        credentials
            .set("OPENAI_API_KEY", Some("synthetic-original"))
            .unwrap();
        std::fs::create_dir(temp.path().join("config.toml")).unwrap();
        let change = patch(
            json!({"secrets":{"OPENAI_API_KEY":"synthetic-new"},"stt":{"silence_duration":0.8}}),
        );
        assert!(store
            .save(store.candidate(&change).unwrap(), change)
            .is_err());
        assert_eq!(
            credentials.get("OPENAI_API_KEY").unwrap().as_deref(),
            Some("synthetic-original")
        );
        assert_eq!(store.document["stt"]["silence_duration"], 0.4);
        assert_eq!(
            std::fs::read_dir(temp.path()).unwrap().count(),
            1,
            "temporary writes are cleaned up"
        );
    }
    #[test]
    fn minimal_legacy_style_keeps_defaults_and_custom_instructions() {
        let temp = tempfile::tempdir().unwrap();
        std::fs::write(temp.path().join("config.toml"),
            "[reply]\ndefault_style = 'custom'\n[[reply.styles]]\nid = 'custom'\ninstruction = 'synthetic instruction'\n").unwrap();
        let mut store = Store::open(
            &Config {
                data_dir: temp.path().into(),
                audio_worker: PathBuf::new(),
                speech_worker: PathBuf::new(),
                model: None,
                legacy_model: None,
                hub_cache: temp.path().join("hub"),
                hub_offline: true,
                punctuation: None,
                python_worker: PathBuf::new(),
            },
            Arc::new(MemorySecrets::default()),
        )
        .unwrap();
        let response = store.response().unwrap();
        assert_eq!(
            response["reply"]["styles"][0],
            json!({"id":"custom","label":"","enabled":true,"priority":100})
        );
        let change = patch(json!({"reply":{"auto_generate":false}}));
        store
            .save(store.candidate(&change).unwrap(), change)
            .unwrap();
        assert_eq!(
            store.document["reply"]["styles"][0]["instruction"],
            "synthetic instruction"
        );
    }
    #[test]
    fn legacy_secret_deletion_does_not_resurrect_after_restart() {
        let temp = tempfile::tempdir().unwrap();
        let primary = Arc::new(MemorySecrets::default());
        let legacy = FileSecrets {
            path: temp.path().join("secrets.toml"),
        };
        legacy
            .set("OPENAI_API_KEY", Some("synthetic-legacy"))
            .unwrap();
        let combined = MigratingSecrets {
            primary: primary.clone(),
            legacy,
        };
        assert_eq!(
            combined.get("OPENAI_API_KEY").unwrap().as_deref(),
            Some("synthetic-legacy")
        );
        combined.set("OPENAI_API_KEY", None).unwrap();
        assert_eq!(primary.get("OPENAI_API_KEY").unwrap(), None);
        assert_eq!(combined.legacy.get("OPENAI_API_KEY").unwrap(), None);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(&combined.legacy.path)
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o777,
                0o600
            );
        }
    }
}
