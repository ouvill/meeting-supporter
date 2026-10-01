use poem_openapi::{Enum, Object};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Serialize, Deserialize, Enum)]
pub enum RecordingRole {
    #[serde(rename = "other")]
    #[oai(rename = "other")]
    Other,
    #[serde(rename = "self")]
    #[oai(rename = "self")]
    SelfInput,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct MeetingListItem {
    pub id: String,
    #[oai(nullable)]
    pub title: Option<String>,
    pub started_at: String,
    #[oai(nullable)]
    pub ended_at: Option<String>,
    #[oai(nullable)]
    pub duration_seconds: Option<i64>,
    pub status: String,
    pub has_ai_note: bool,
    pub has_recording: bool,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct MeetingListPage {
    pub items: Vec<MeetingListItem>,
    pub total: i64,
    pub limit: i64,
    pub offset: i64,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct TurnItem {
    pub id: String,
    pub sequence: i64,
    pub speaker: String,
    pub text: String,
    #[oai(nullable)]
    pub speaker_id: Option<String>,
    #[oai(nullable)]
    pub created_at: Option<String>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct ReplySuggestionItem {
    pub id: String,
    pub target_turn_id: String,
    pub sequence: i64,
    pub agent_id: String,
    pub agent_label: String,
    pub text: String,
    #[oai(nullable)]
    pub created_at: Option<String>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct RecordingAssetItem {
    pub id: String,
    pub role: RecordingRole,
    pub format: String,
    pub sample_rate: i64,
    pub channels: i64,
    pub started_at: String,
    #[oai(nullable)]
    pub ended_at: Option<String>,
    #[oai(nullable)]
    pub size_bytes: Option<i64>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct MeetingDetail {
    pub id: String,
    #[oai(nullable)]
    pub title: Option<String>,
    pub started_at: String,
    #[oai(nullable)]
    pub ended_at: Option<String>,
    #[oai(nullable)]
    pub duration_seconds: Option<i64>,
    pub status: String,
    pub ai_note: String,
    pub minutes: String,
    #[oai(nullable)]
    pub created_at: Option<String>,
    #[oai(nullable)]
    pub updated_at: Option<String>,
    pub turns: Vec<TurnItem>,
    pub reply_suggestions: Vec<ReplySuggestionItem>,
    pub recording_assets: Vec<RecordingAssetItem>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct RecordingCleanupRequest {
    #[oai(nullable)]
    pub cutoff_date: Option<String>,
    #[oai(nullable)]
    pub max_total_bytes: Option<i64>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct RecordingCleanupPreviewResponse {
    pub candidate_meeting_ids: Vec<String>,
    pub delete_count: i64,
    pub delete_recording_bytes: i64,
    pub total_recording_bytes_before: i64,
    pub total_recording_bytes_after: i64,
}

#[derive(Clone, Serialize, Deserialize, Object)]
pub struct RecordingCleanupExecuteResponse {
    pub candidate_meeting_ids: Vec<String>,
    pub delete_count: i64,
    pub delete_recording_bytes: i64,
    pub total_recording_bytes_before: i64,
    pub total_recording_bytes_after: i64,
    pub deleted_meeting_ids: Vec<String>,
    pub failed_meeting_ids: Vec<String>,
    pub skipped_meeting_ids: Vec<String>,
}

#[derive(Clone, Serialize, Deserialize, Object)]
#[serde(deny_unknown_fields)]
#[oai(deny_unknown_fields)]
pub struct TitleUpdateRequest {
    pub title: String,
}
