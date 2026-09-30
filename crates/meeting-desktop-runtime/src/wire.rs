use meeting_media_runtime::wire::Role;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize, Serialize, Default)]
#[serde(
    rename_all(deserialize = "camelCase", serialize = "snake_case"),
    deny_unknown_fields
)]
pub struct Context {
    #[serde(default)]
    pub scenario: String,
    #[serde(default)]
    pub user_role: String,
    pub counterpart_role: Option<String>,
    #[serde(default)]
    pub objective: String,
    pub background: Option<String>,
    pub tone: Option<String>,
    pub constraints: Option<String>,
    pub custom_instructions: Option<String>,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Reference {
    pub id: String,
    pub name: String,
    pub mime_type: String,
    pub size_bytes: u64,
    pub text: Option<String>,
    pub content_base64: Option<String>,
    #[serde(rename = "status", default)]
    pub _status: Option<ReferenceClientStatus>,
    #[serde(rename = "error", default)]
    pub _error: Option<String>,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceClientStatus {
    Queued,
    Parsed,
    Failed,
}
#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    GenerateReply {
        generation_id: String,
        target_utterance_id: Option<String>,
        #[serde(default)]
        mode: SuggestionMode,
    },
    CancelReply {
        generation_id: String,
        target_utterance_id: String,
    },
    ReloadContext {},
    InitStt {},
    ShutdownStt {},
    StopMeeting {},
    StartMeeting {
        meeting_context: Option<Context>,
        #[serde(default)]
        references: Vec<Reference>,
    },
    SetDevice {
        role: Role,
        device: Option<String>,
    },
    ManualSpeech {
        text: String,
    },
    UserReply {
        text: String,
    },
}
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Device {
    pub index: String,
    pub name: String,
    pub is_monitor: bool,
    #[serde(default)]
    pub is_default: bool,
}
#[derive(Debug, Clone, Serialize)]
pub struct TurnItem {
    pub id: String,
    pub speaker: String,
    pub text: String,
    pub speaker_id: Option<String>,
}
#[derive(Debug, Clone, Serialize)]
pub struct ReplyAgent {
    pub id: String,
    pub label: String,
    pub enabled: bool,
    pub priority: i64,
}
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Event {
    SuggestionsStart {
        #[serde(flatten)]
        meta: ReplyMeta,
    },
    ReplyChunk {
        #[serde(flatten)]
        meta: ReplyMeta,
        text: String,
        #[serde(rename = "final")]
        final_chunk: bool,
    },
    SuggestionError {
        #[serde(flatten)]
        meta: ReplyMeta,
        text: String,
    },
    ReplyCancelResult {
        generation_id: String,
        target_utterance_id: String,
        status: CancelStatus,
        cancelled_suggestion_ids: Vec<String>,
    },
    Status {
        text: String,
    },
    Error {
        text: String,
    },
    MeetingState {
        running: bool,
        saved: bool,
    },
    SttState {
        backend: String,
        initialized: bool,
        initializing: bool,
    },
    DevicesList {
        devices: Vec<Device>,
        current_self: Option<String>,
        current_other: Option<String>,
    },
    AgentSettings {
        reply_enabled: bool,
        reply_auto_generate: bool,
        reply_agents: Vec<ReplyAgent>,
    },
    HistoryReset {
        items: Vec<TurnItem>,
    },
    SessionInfo {
        id: String,
        started_at: String,
        ended_at: Option<String>,
        is_active: bool,
    },
    AudioLevel {
        role: Role,
        level: f64,
    },
    StreamInfo {
        role: Role,
        device: String,
        rate: u32,
    },
    SttFinal {
        role: Role,
        text: String,
        speaker_id: Option<String>,
        utterance_id: String,
    },
}

#[derive(Debug, Clone, Copy, Default, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SuggestionMode {
    #[default]
    Normal,
    Polite,
    Short,
    Clarify,
    BuyTime,
    PushBack,
    Summarize,
}
impl SuggestionMode {
    pub(crate) fn instruction(self) -> &'static str {
        match self {
            Self::Normal => "自然な返答にしてください。",
            Self::Polite => "丁寧で角が立たない表現にしてください。",
            Self::Short => "返答を短い1文にしてください。",
            Self::Clarify => "不明点を確認する短い質問にしてください。",
            Self::BuyTime => "考える時間を確保する自然な一言にしてください。",
            Self::PushBack => "懸念や異なる意見を丁寧に伝えてください。",
            Self::Summarize => "直近の論点を短くまとめる発言にしてください。",
        }
    }
}
#[derive(Debug, Clone, Serialize)]
pub struct ReplyMeta {
    pub agent_id: String,
    pub agent_label: String,
    pub agent_priority: i64,
    pub generation_id: String,
    pub suggestion_id: String,
    pub target_utterance_id: String,
    pub target_role: String,
    pub mode: SuggestionMode,
}
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CancelStatus {
    Applied,
    NotApplied,
}
