use super::AiError;
use crate::{settings::Store, Error};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Provider {
    Openai,
    Gemini,
    Anthropic,
    Ollama,
}
impl Provider {
    fn defaults(self) -> (&'static str, &'static str, &'static str, &'static str) {
        match self {
            Self::Openai => (
                "openai",
                "gpt-5.4-mini",
                "https://api.openai.com/v1",
                "OPENAI_API_KEY",
            ),
            Self::Gemini => (
                "gemini",
                "gemini-3.1-flash-lite",
                "https://generativelanguage.googleapis.com",
                "GEMINI_API_KEY",
            ),
            Self::Anthropic => (
                "anthropic",
                "claude-haiku-4-5-20251001",
                "https://api.anthropic.com",
                "ANTHROPIC_API_KEY",
            ),
            Self::Ollama => ("ollama", "qwen3", "http://localhost:11434/v1", ""),
        }
    }
}
// Deliberately no Debug/Serialize: a resolved route owns a credential.
pub(crate) struct Route {
    pub provider: Provider,
    pub model: String,
    pub base_url: String,
    pub key: Option<String>,
}
#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Assignments {
    pub reply: Option<String>,
    pub minutes: Option<String>,
}
pub(crate) fn assignments(store: &Store) -> Result<Assignments, Error> {
    // Ignore the retired info assignment in saved settings, but reject it in API requests.
    let mut saved = store.document["ai"]["assignments"].clone();
    if let Some(values) = saved.as_object_mut() {
        values.remove("info");
    }
    serde_json::from_value(saved).map_err(|_| AiError::Configuration.into())
}
fn provider(id: &str) -> Result<Provider, AiError> {
    match id {
        "openai" => Ok(Provider::Openai),
        "gemini" => Ok(Provider::Gemini),
        "anthropic" => Ok(Provider::Anthropic),
        "ollama" => Ok(Provider::Ollama),
        _ => Err(AiError::Unsupported),
    }
}
pub(crate) fn resolve(store: &Store, id: &str) -> Result<Route, Error> {
    let provider = provider(id)?;
    let (_, default_model, default_url, key_name) = provider.defaults();
    let document = &store.document;
    let config = &document["ai"]["routes"][id];
    if config.get("runtime").is_some_and(|v| v != "pydantic-ai") {
        return Err(AiError::Unsupported.into());
    }
    let model = config
        .get("model")
        .map(|v| v.as_str().ok_or(AiError::Configuration))
        .transpose()?
        .unwrap_or(default_model);
    if model.trim().is_empty() || model.len() > 256 {
        return Err(AiError::Configuration.into());
    }
    let custom = &document["providers"][id];
    // Do not silently send credentials to a different protocol or custom key reference.
    if custom
        .get("kind")
        .is_some_and(|v| v != id && !(id == "gemini" && v == "google-gla"))
        || custom.get("key_ref").is_some_and(|v| v != key_name)
    {
        return Err(AiError::Unsupported.into());
    }
    let url_value = if provider == Provider::Ollama {
        document["ollama"].get("base_url")
    } else {
        custom.get("base_url")
    };
    let base_url = url_value
        .map(|v| v.as_str().ok_or(AiError::Configuration))
        .transpose()?
        .unwrap_or(default_url);
    let url = reqwest::Url::parse(base_url).map_err(|_| AiError::Configuration)?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(AiError::Configuration.into());
    }
    let key = if key_name.is_empty() {
        None
    } else {
        store.secret(key_name)?.filter(|s| !s.trim().is_empty())
    };
    if provider != Provider::Ollama && key.is_none() {
        return Err(AiError::Configuration.into());
    }
    Ok(Route {
        provider,
        model: model.into(),
        base_url: base_url.trim_end_matches('/').into(),
        key,
    })
}
fn local_url(url: &str) -> bool {
    reqwest::Url::parse(url)
        .ok()
        .and_then(|u| u.host_str().map(str::to_owned))
        .is_some_and(|h| {
            h == "localhost"
                || h.trim_matches(['[', ']'])
                    .parse::<std::net::IpAddr>()
                    .is_ok_and(|ip| ip.is_loopback())
        })
}
pub(crate) async fn catalog(store: Store) -> Result<Value, Error> {
    let assigned = assignments(&store)?;
    let resolved = tokio::task::spawn_blocking(move || {
        [
            Provider::Openai,
            Provider::Gemini,
            Provider::Anthropic,
            Provider::Ollama,
        ]
        .into_iter()
        .map(|p| (p, resolve(&store, p.defaults().0)))
        .collect::<Vec<_>>()
    })
    .await
    .map_err(|_| Error::Closed)?;
    let mut rows = Vec::new();
    for (p, route) in resolved {
        let (id, _, _, _) = p.defaults();
        let (ready, code, message, action) = match &route {
            Ok(route) if p == Provider::Ollama => match ollama_models(&route.base_url).await {
                Ok(models) if models.iter().any(|m| m == &route.model) => {
                    ("ready", "", "利用できます。", "none")
                }
                Ok(_) => (
                    "setup_required",
                    "OLLAMA_MODEL_NOT_INSTALLED",
                    "選択したモデルをOllamaに導入してください。",
                    "configure",
                ),
                Err(_) => (
                    "unavailable",
                    "OLLAMA_UNAVAILABLE",
                    "Ollamaへ接続できません。",
                    "start",
                ),
            },
            Ok(_) => ("ready", "", "認証情報が設定されています。", "none"),
            Err(Error::Ai(AiError::Unsupported)) => (
                "unavailable",
                "RUST_ROUTE_UNSUPPORTED",
                "この接続設定はRustへの移植中です。",
                "configure",
            ),
            Err(_) => (
                "setup_required",
                "AI_ROUTE_CONFIGURATION",
                "接続先・モデル・認証情報を設定してください。",
                "configure",
            ),
        };
        let selectable = code != "RUST_ROUTE_UNSUPPORTED";
        let selected = selectable
            && [&assigned.reply, &assigned.minutes]
                .iter()
                .any(|a| a.as_deref() == Some(id));
        let label = match p {
            Provider::Openai => "OpenAI",
            Provider::Gemini => "Google Gemini",
            Provider::Anthropic => "Anthropic",
            Provider::Ollama => "Ollama",
        };
        let location = if p == Provider::Ollama {
            if route.as_ref().is_ok_and(|r| local_url(&r.base_url)) {
                "local"
            } else {
                "external"
            }
        } else {
            "cloud"
        };
        rows.push(json!({
            "id": id, "kind": if p == Provider::Ollama { "local" } else { "byok" },
            "label": label, "description": "設定したモデルで返答案を生成します。",
            "availability": "experimental", "readiness": ready,
            "selectable": selectable, "selected": selected,
            "data_location": location, "billing_owner": "user",
            "capabilities": ["reply", "stream", "cancel"],
            "reason_code": code, "message": message, "action": action,
        }));
    }
    for (id, label) in [
        ("codex", "Codex"),
        ("acp", "外部エージェント連携"),
        ("managed", "Meeting Supporter AI"),
    ] {
        rows.push(json!({
            "id": id, "kind": if id == "managed" { "managed" } else { "subscription_app" },
            "label": label, "description": "", "availability": "planned",
            "readiness": if id == "managed" { "not_offered" } else { "unavailable" },
            "selectable": false, "selected": false, "data_location": "unknown",
            "billing_owner": "none", "capabilities": [], "reason_code": "RUST_ROUTE_UNSUPPORTED",
            "message": "この経路はRust構成ではまだ利用できません。", "action": "none",
        }));
    }
    Ok(json!({"routes":rows,"assignments":assigned}))
}
pub(crate) async fn ollama_models(base_url: &str) -> Result<Vec<String>, AiError> {
    let url = reqwest::Url::parse(base_url).map_err(|_| AiError::Configuration)?;
    if !matches!(url.scheme(), "http" | "https")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(AiError::Configuration);
    }
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| AiError::Provider)?;
    let response = client
        .get(format!("{}/models", base_url.trim_end_matches('/')))
        .send()
        .await
        .map_err(|_| AiError::Provider)?
        .error_for_status()
        .map_err(|_| AiError::Provider)?;
    use futures_util::StreamExt;
    let mut stream = response.bytes_stream();
    let mut body = Vec::new();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| AiError::Provider)?;
        if body.len() + chunk.len() > 1024 * 1024 {
            return Err(AiError::Provider);
        }
        body.extend_from_slice(&chunk);
    }
    #[derive(Deserialize)]
    struct Models {
        data: Vec<Model>,
    }
    #[derive(Deserialize)]
    struct Model {
        id: String,
    }
    let models: Models = serde_json::from_slice(&body).map_err(|_| AiError::Provider)?;
    Ok(models.data.into_iter().map(|m| m.id).collect())
}
