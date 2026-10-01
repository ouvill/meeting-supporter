use poem_openapi::{Enum, Object};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum AgentDistribution {
    #[serde(rename = "binary")]
    #[oai(rename = "binary")]
    Binary,
    #[serde(rename = "npm")]
    #[oai(rename = "npm")]
    Npm,
    #[serde(rename = "unsupported")]
    #[oai(rename = "unsupported")]
    Unsupported,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct AgentAuthMethod {
    pub id: String,
    pub name: String,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct AgentStatus {
    pub ready: bool,
    pub message: String,
    pub auth_methods: Vec<AgentAuthMethod>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct RegistryAgent {
    pub id: String,
    pub name: String,
    pub description: String,
    pub authors: Vec<String>,
    pub version: String,
    #[oai(nullable)]
    pub installed_version: Option<String>,
    #[oai(nullable)]
    pub update_version: Option<String>,
    pub supported: bool,
    pub distribution: AgentDistribution,
    pub status: AgentStatus,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct AgentCatalog {
    pub supported: bool,
    pub agents: Vec<RegistryAgent>,
    pub update_count: i64,
    #[oai(nullable)]
    pub checked_at: Option<i64>,
    #[oai(nullable)]
    pub update_message: Option<String>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct AgentConnectRequest {
    #[oai(nullable)]
    pub method: Option<String>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct AgentInstallResponse {
    pub ok: bool,
    pub changed: bool,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct AgentUpdateResult {
    pub id: String,
    pub name: String,
    pub updated: bool,
    #[oai(nullable)]
    pub error: Option<String>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct AgentUpdatesResponse {
    pub results: Vec<AgentUpdateResult>,
}
