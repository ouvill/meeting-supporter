//! Versioned Rust settings. Credentials are owned by the OS credential store.
mod schema;
use crate::{ai::routes::Provider, models::catalog::Backend, Config, Error};
use meeting_media_runtime::wire::{Recognizer, SpeechConfig};
use schema::Document;
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::{
    collections::BTreeMap,
    io::Write,
    path::{Path, PathBuf},
    sync::Arc,
};

pub(crate) use schema::UsageBudget;
pub const FILE_NAME: &str = "settings.toml";
pub const SECRET_KEYS: [&str; 3] = ["GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"];

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
    pub ai_models: Option<Map<String, Value>>,
    pub stt: Option<Map<String, Value>>,
    pub audio: Option<Map<String, Value>>,
    pub reply: Option<Map<String, Value>>,
    pub ollama: Option<Map<String, Value>>,
    pub context: Option<Map<String, Value>>,
    pub usage_budget: Option<Map<String, Value>>,
    pub recording_retention: Option<Map<String, Value>>,
    pub secrets: Option<BTreeMap<String, Option<String>>>,
    pub delete_secrets: Option<Vec<String>>,
}
#[derive(Clone)]
pub struct Store {
    directory: PathBuf,
    pub(crate) document: Document,
    secrets: Arc<dyn Secrets>,
}
fn invalid() -> Error {
    Error::Settings
}
impl Store {
    pub(crate) fn secret(&self, key: &str) -> Result<Option<String>, Error> {
        self.secrets.get(key)
    }

    pub fn open(config: &Config, secrets: Arc<dyn Secrets>) -> Result<Self, Error> {
        let document = match std::fs::read_to_string(config.data_dir.join(FILE_NAME)) {
            Ok(source) => Document::parse(&source)?,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Document::default(),
            Err(error) => return Err(error.into()),
        };
        Ok(Self {
            directory: config.data_dir.clone(),
            document,
            secrets,
        })
    }
    pub(crate) fn speech(&self) -> SpeechConfig {
        let stt = &self.document.stt;
        SpeechConfig {
            vad_threshold: stt.vad_sensitivity as f32,
            silence_seconds: stt.silence_duration,
            min_voiced_ms: stt.min_voiced_ms,
            min_voiced_ratio: stt.min_voiced_ratio,
            min_rms_dbfs: stt.min_rms_dbfs,
        }
    }
    pub(crate) fn recognizer(&self) -> Recognizer {
        let stt = &self.document.stt;
        match stt.backend {
            Backend::Reazonspeech => Recognizer::Reazonspeech,
            Backend::Whisper => Recognizer::Whisper {
                device: stt.device,
                language: stt.language,
            },
        }
    }
    pub(crate) fn whisper_model(&self) -> crate::models::catalog::Whisper {
        self.document.stt.whisper_model
    }
    pub fn backend(&self) -> String {
        match self.document.stt.backend {
            Backend::Reazonspeech => "reazonspeech",
            Backend::Whisper => "whisper",
        }
        .into()
    }
    pub(crate) fn context_dir(&self) -> PathBuf {
        self.document
            .context
            .dir_override
            .as_deref()
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| self.directory.join("context"))
    }
    pub(crate) fn route_model(&self, provider: Provider) -> &str {
        self.document
            .ai
            .routes
            .get(&provider)
            .and_then(|r| r.model.as_deref())
            .unwrap_or(provider.defaults().1)
    }
    pub(crate) fn route_url(&self, provider: Provider) -> &str {
        self.document
            .ai
            .routes
            .get(&provider)
            .and_then(|r| r.base_url.as_deref())
            .unwrap_or(provider.defaults().2)
    }
    pub(crate) fn agent_event(&self) -> crate::wire::Event {
        let reply = &self.document.reply;
        crate::wire::Event::AgentSettings {
            reply_enabled: reply.enabled,
            reply_auto_generate: reply.auto_generate,
            reply_agents: reply
                .styles
                .iter()
                .map(|style| crate::wire::ReplyAgent {
                    id: style.id.clone(),
                    label: style.label.clone(),
                    enabled: style.enabled,
                    priority: style.priority,
                })
                .collect(),
        }
    }
    pub fn response(&self) -> Result<Value, Error> {
        let mut secrets = Map::new();
        for key in SECRET_KEYS {
            // Route readiness reports credential-store failures. Local settings stay usable.
            let configured = match self.secrets.get(key) {
                Ok(value) => value.is_some_and(|v| !v.is_empty()),
                Err(Error::Secrets) => false,
                Err(error) => return Err(error),
            };
            secrets.insert(key.into(), json!(configured));
        }
        let document = &self.document;
        let styles: Vec<_> = document.reply.styles.iter().map(|style|
            json!({"id":style.id,"label":style.label,"enabled":style.enabled,"priority":style.priority})).collect();
        Ok(json!({
            "stt":document.stt,"audio":document.audio,
            "ollama":{"base_url":self.route_url(Provider::Ollama)},
            "ai_models":{
                "openai":self.route_model(Provider::Openai),
                "gemini":self.route_model(Provider::Gemini),
                "anthropic":self.route_model(Provider::Anthropic),
                "ollama":self.route_model(Provider::Ollama),
            },
            "reply":{"enabled":document.reply.enabled,"auto_generate":document.reply.auto_generate,
                "default_style":document.reply.default_style,"styles":styles},
            "secrets":secrets,"providers":[],"data_dir":self.directory,"context_dir":self.context_dir(),
            "usage":{"budget":document.usage_budget,
                "current_meeting":{"input_tokens":0,"output_tokens":0,"estimated_cost_jpy":0.0,"request_count":0},
                "current_month":crate::usage::month(&self.directory.join("usage.jsonl"))?,
                "billing_mode":match document.ai.assignments.reply.as_deref() {
                    Some(id) if id.starts_with("acp:")=>"unknown",Some("ollama")=>"local",
                    Some("openai"|"gemini"|"anthropic")=>"byok",_=>"unassigned"}},
            "recording_retention":document.recording_retention,
        }))
    }
    pub(crate) fn candidate(&self, patch: &Patch) -> Result<Document, Error> {
        let mut document = serde_json::to_value(&self.document)?;
        for (name, section) in [
            ("stt", &patch.stt),
            ("audio", &patch.audio),
            ("context", &patch.context),
            ("usage_budget", &patch.usage_budget),
            ("recording_retention", &patch.recording_retention),
        ] {
            if let Some(section) = section {
                for (key, value) in section {
                    let field = document[name].get_mut(key).ok_or_else(invalid)?;
                    if !value.is_null() || name == "recording_retention" || name == "context" {
                        *field = value.clone();
                    }
                }
            }
        }
        if let Some(ollama) = &patch.ollama {
            for (key, value) in ollama {
                if key != "base_url" {
                    return Err(invalid());
                }
                if !value.is_null() {
                    let routes = document["ai"]["routes"]
                        .as_object_mut()
                        .ok_or_else(invalid)?;
                    routes.entry("ollama").or_insert_with(|| json!({}))[key] = value.clone();
                }
            }
        }
        if let Some(models) = &patch.ai_models {
            for (key, value) in models {
                if !matches!(key.as_str(), "openai" | "gemini" | "anthropic" | "ollama")
                    || value.as_str().is_none()
                {
                    return Err(invalid());
                }
                let routes = document["ai"]["routes"]
                    .as_object_mut()
                    .ok_or_else(invalid)?;
                routes.entry(key).or_insert_with(|| json!({}))["model"] = value.clone();
            }
        }
        if let Some(reply) = &patch.reply {
            for (key, value) in reply {
                if !matches!(
                    key.as_str(),
                    "enabled" | "auto_generate" | "default_style" | "styles"
                ) {
                    return Err(invalid());
                }
                if value.is_null() {
                    continue;
                }
                if key == "styles" {
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
                } else {
                    document["reply"][key] = value.clone();
                }
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
        let document: Document = serde_json::from_value(document).map_err(|_| invalid())?;
        document.validate()?;
        Ok(document)
    }
    pub(crate) fn audio_changed(&self, candidate: &Document) -> bool {
        self.document.stt != candidate.stt || self.document.audio != candidate.audio
    }
    pub(crate) fn save(&mut self, candidate: Document, patch: Patch) -> Result<(), Error> {
        candidate.validate()?;
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
        let text = toml::to_string_pretty(&candidate).map_err(|_| invalid())?;
        let result = (|| {
            for (key, value) in &changes {
                self.secrets.set(key, value.as_deref())?;
            }
            atomic_write(&self.directory.join(FILE_NAME), text.as_bytes())
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
    file.write_all(bytes)?;
    file.as_file().sync_all()?;
    file.persist(path).map_err(|error| Error::Io(error.error))?;
    Ok(())
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
            document: Document::default(),
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
            json!({"secrets":{"GEMINI_API_KEY":"synthetic-new","OPENAI_API_KEY":"synthetic-failure"},"stt":{"silence_duration":0.8}}),
        );
        let candidate = store.candidate(&change).unwrap();
        assert!(store.save(candidate, change).is_err());
        assert_eq!(
            credentials.get("GEMINI_API_KEY").unwrap().as_deref(),
            Some("synthetic-original")
        );
        assert!(!temp.path().join(FILE_NAME).exists());
        assert_eq!(store.document.stt.silence_duration, 0.4);
        let change = patch(json!({"secrets":{"GEMINI_API_KEY":"synthetic-new"}}));
        store
            .save(store.candidate(&change).unwrap(), change)
            .unwrap();
        let response = store.response().unwrap();
        assert_eq!(response["secrets"]["GEMINI_API_KEY"], true);
        assert!(!response.to_string().contains("synthetic-new"));
        assert!(!std::fs::read_to_string(temp.path().join(FILE_NAME))
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
        std::fs::create_dir(temp.path().join(FILE_NAME)).unwrap();
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
        assert_eq!(store.document.stt.silence_duration, 0.4);
        assert_eq!(
            std::fs::read_dir(temp.path()).unwrap().count(),
            1,
            "temporary writes are cleaned up"
        );
    }
    fn config(dir: &Path) -> Config {
        Config {
            agent_updates: false,
            data_dir: dir.into(),
            audio_worker: PathBuf::new(),
            speech_worker: PathBuf::new(),
            model: None,
            legacy_model: None,
            hub_cache: dir.join("hub"),
            hub_offline: true,
            punctuation: None,
            python_worker: PathBuf::new(),
        }
    }

    #[test]
    fn unsupported_versions_are_not_read_or_overwritten() {
        for source in [
            "",
            "[ai]\nschema_version = 2\n",
            "schema_version = 0\n",
            "schema_version = 2\n",
            "schema_version = '1'\n",
        ] {
            let temp = tempfile::tempdir().unwrap();
            let path = temp.path().join(FILE_NAME);
            std::fs::write(&path, source).unwrap();
            assert!(matches!(
                Store::open(&config(temp.path()), Arc::new(MemorySecrets::default())),
                Err(Error::SettingsVersion)
            ));
            assert_eq!(std::fs::read_to_string(path).unwrap(), source);
        }
    }

    #[test]
    fn current_schema_rejects_unknown_fields_retired_routes_and_invalid_values() {
        for fields in [
            "unknown = true",
            "[ai]\nschema_version = 2",
            "[ai.assignments]\nminutes = 'ollama'",
            "[ai.assignments]\nreply = 'codex'",
            "[ai.routes.codex]\nmodel = 'synthetic'",
            "[ai.routes.openai]\nruntime = 'pydantic-ai'",
            "[ai.routes.openai]\nbase_url = 'https://user:synthetic@example.invalid'",
            "[ai.routes.openai]\nbase_url = 'https://example.invalid?key=synthetic'",
            "[providers.openai]\nbase_url = 'https://example.invalid'",
            "[stt]\nbackend = 'openai'",
            "[stt]\nno_speech_threshold = 0.6",
            "[stt]\ndevice = 'cuda'",
            "[stt]\nvad_engine = 'webrtc'",
            "[stt]\nvad_sensitivity = 9.0",
            "[stt]\nmin_voiced_ms = true",
            "[stt]\nsilence_duration = nan",
            "[audio]\nsample_rate = 48000",
            "[usage_budget]\nmeeting_limit_jpy = -1",
            "[recording_retention]\ncutoff_date = 'invalid'",
            "[reply]\ndefault_style = 'missing'",
        ] {
            let source = format!("schema_version = 1\n{fields}\n");
            assert!(
                matches!(Document::parse(&source), Err(Error::Settings)),
                "{fields}"
            );
        }
    }

    #[test]
    fn current_settings_round_trip_without_changing_custom_reply_instructions() {
        let temp = tempfile::tempdir().unwrap();
        std::fs::write(temp.path().join(FILE_NAME), concat!(
            "schema_version = 1\n[reply]\ndefault_style = 'custom'\n",
            "[[reply.styles]]\nid = 'custom'\nlabel = 'Custom'\nenabled = true\npriority = 20\ninstruction = 'synthetic instruction'\n",
            "[ai.routes.ollama]\nmodel = 'synthetic-model'\nbase_url = 'http://127.0.0.1:11434/v1'\n",
        )).unwrap();
        let credentials = Arc::new(MemorySecrets::default());
        let config = config(temp.path());
        let mut store = Store::open(&config, credentials.clone()).unwrap();
        let change = patch(
            json!({"reply":{"auto_generate":true},"context":{"dir_override":null},"recording_retention":{"cutoff_date":null,"max_total_bytes":null}}),
        );
        store
            .save(store.candidate(&change).unwrap(), change)
            .unwrap();
        let store = Store::open(&config, credentials).unwrap();
        assert_eq!(store.document.schema_version, 1);
        assert!(store.document.reply.auto_generate);
        assert_eq!(
            store.document.reply.styles[0].instruction,
            "synthetic instruction"
        );
        assert_eq!(store.route_model(Provider::Ollama), "synthetic-model");
        assert_eq!(
            store.route_url(Provider::Ollama),
            "http://127.0.0.1:11434/v1"
        );
        assert!(store.document.recording_retention.cutoff_date.is_none());
    }
}
