use poem_openapi::{Enum, Object};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum ProviderKind {
    #[serde(rename = "google-gla")]
    #[oai(rename = "google-gla")]
    GoogleGla,
    #[serde(rename = "google-vertex")]
    #[oai(rename = "google-vertex")]
    GoogleVertex,
    #[serde(rename = "openai")]
    #[oai(rename = "openai")]
    Openai,
    #[serde(rename = "openai-chat")]
    #[oai(rename = "openai-chat")]
    OpenaiChat,
    #[serde(rename = "openai-responses")]
    #[oai(rename = "openai-responses")]
    OpenaiResponses,
    #[serde(rename = "anthropic")]
    #[oai(rename = "anthropic")]
    Anthropic,
    #[serde(rename = "openai-compatible")]
    #[oai(rename = "openai-compatible")]
    OpenaiCompatible,
    #[serde(rename = "ollama")]
    #[oai(rename = "ollama")]
    Ollama,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum DataLocation {
    #[serde(rename = "cloud")]
    #[oai(rename = "cloud")]
    Cloud,
    #[serde(rename = "external")]
    #[oai(rename = "external")]
    External,
    #[serde(rename = "local")]
    #[oai(rename = "local")]
    Local,
    #[serde(rename = "unknown")]
    #[oai(rename = "unknown")]
    Unknown,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum RouteKind {
    #[serde(rename = "managed")]
    #[oai(rename = "managed")]
    Managed,
    #[serde(rename = "subscription_app")]
    #[oai(rename = "subscription_app")]
    SubscriptionApp,
    #[serde(rename = "local")]
    #[oai(rename = "local")]
    Local,
    #[serde(rename = "byok")]
    #[oai(rename = "byok")]
    Byok,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum RouteAvailability {
    #[serde(rename = "available")]
    #[oai(rename = "available")]
    Available,
    #[serde(rename = "experimental")]
    #[oai(rename = "experimental")]
    Experimental,
    #[serde(rename = "planned")]
    #[oai(rename = "planned")]
    Planned,
    #[serde(rename = "unavailable")]
    #[oai(rename = "unavailable")]
    Unavailable,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum RouteReadiness {
    #[serde(rename = "ready")]
    #[oai(rename = "ready")]
    Ready,
    #[serde(rename = "setup_required")]
    #[oai(rename = "setup_required")]
    SetupRequired,
    #[serde(rename = "unavailable")]
    #[oai(rename = "unavailable")]
    Unavailable,
    #[serde(rename = "error")]
    #[oai(rename = "error")]
    Error,
    #[serde(rename = "not_offered")]
    #[oai(rename = "not_offered")]
    NotOffered,
    #[serde(rename = "unknown")]
    #[oai(rename = "unknown")]
    Unknown,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum BillingOwner {
    #[serde(rename = "app")]
    #[oai(rename = "app")]
    App,
    #[serde(rename = "external_subscription")]
    #[oai(rename = "external_subscription")]
    ExternalSubscription,
    #[serde(rename = "user")]
    #[oai(rename = "user")]
    User,
    #[serde(rename = "none")]
    #[oai(rename = "none")]
    None,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum RouteCapability {
    #[serde(rename = "reply")]
    #[oai(rename = "reply")]
    Reply,
    #[serde(rename = "stream")]
    #[oai(rename = "stream")]
    Stream,
    #[serde(rename = "cancel")]
    #[oai(rename = "cancel")]
    Cancel,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum RouteAction {
    #[serde(rename = "none")]
    #[oai(rename = "none")]
    None,
    #[serde(rename = "configure")]
    #[oai(rename = "configure")]
    Configure,
    #[serde(rename = "install")]
    #[oai(rename = "install")]
    Install,
    #[serde(rename = "login")]
    #[oai(rename = "login")]
    Login,
    #[serde(rename = "start")]
    #[oai(rename = "start")]
    Start,
    #[serde(rename = "sign_in")]
    #[oai(rename = "sign_in")]
    SignIn,
    #[serde(rename = "subscribe")]
    #[oai(rename = "subscribe")]
    Subscribe,
    #[serde(rename = "manage_billing")]
    #[oai(rename = "manage_billing")]
    ManageBilling,
    #[serde(rename = "view_usage")]
    #[oai(rename = "view_usage")]
    ViewUsage,
    #[serde(rename = "retry")]
    #[oai(rename = "retry")]
    Retry,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum RouteServiceTier {
    #[serde(rename = "priority")]
    #[oai(rename = "priority")]
    Priority,
    #[serde(rename = "standard")]
    #[oai(rename = "standard")]
    Standard,
    #[serde(rename = "unknown")]
    #[oai(rename = "unknown")]
    Unknown,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum ConnectionProvider {
    #[serde(rename = "openai")]
    #[oai(rename = "openai")]
    Openai,
    #[serde(rename = "gemini")]
    #[oai(rename = "gemini")]
    Gemini,
    #[serde(rename = "anthropic")]
    #[oai(rename = "anthropic")]
    Anthropic,
}

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum ConnectionStatus {
    #[serde(rename = "verified")]
    #[oai(rename = "verified")]
    Verified,
    #[serde(rename = "invalid")]
    #[oai(rename = "invalid")]
    Invalid,
    #[serde(rename = "unavailable")]
    #[oai(rename = "unavailable")]
    Unavailable,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct RouteReadModel {
    pub id: String,
    pub kind: RouteKind,
    pub label: String,
    pub description: String,
    pub availability: RouteAvailability,
    pub readiness: RouteReadiness,
    pub selectable: bool,
    pub selected: bool,
    pub data_location: DataLocation,
    pub billing_owner: BillingOwner,
    pub capabilities: Vec<RouteCapability>,
    #[oai(nullable)]
    pub reason_code: Option<String>,
    pub message: String,
    pub action: RouteAction,
    #[oai(nullable)]
    pub service_tier: Option<RouteServiceTier>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct RouteAssignmentsReadModel {
    #[oai(nullable)]
    pub reply: Option<String>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct RouteCatalogResponse {
    pub routes: Vec<RouteReadModel>,
    pub assignments: RouteAssignmentsReadModel,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct RouteAssignmentsUpdate {
    #[oai(nullable)]
    pub reply: Option<String>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct ConnectionTestRequest {
    pub provider: ConnectionProvider,
    #[oai(nullable)]
    pub api_key: Option<String>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct ConnectionTestResponse {
    pub ok: bool,
    pub status: ConnectionStatus,
    pub message: String,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct AiModelOption {
    pub id: String,
    pub label: String,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct AiModelsResponse {
    pub ok: bool,
    pub provider: ConnectionProvider,
    pub models: Vec<AiModelOption>,
    #[oai(nullable)]
    pub message: Option<String>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct OllamaModelsResponse {
    pub ok: bool,
    pub base_url: String,
    pub models: Vec<String>,
    #[oai(nullable)]
    pub message: Option<String>,
}
