//! Storage DTOs preserve existing SQLite values, including naive timestamps.
use serde::{Deserialize, Deserializer, Serialize};

#[derive(Debug, Clone, Serialize)]
#[serde(transparent)]
pub struct Timestamp(String);
impl sqlx::Type<sqlx::Sqlite> for Timestamp {
    fn type_info() -> sqlx::sqlite::SqliteTypeInfo {
        <String as sqlx::Type<sqlx::Sqlite>>::type_info()
    }
    fn compatible(info: &sqlx::sqlite::SqliteTypeInfo) -> bool {
        <String as sqlx::Type<sqlx::Sqlite>>::compatible(info)
    }
}
impl<'r> sqlx::Decode<'r, sqlx::Sqlite> for Timestamp {
    fn decode(value: sqlx::sqlite::SqliteValueRef<'r>) -> Result<Self, sqlx::error::BoxDynError> {
        let text = <String as sqlx::Decode<sqlx::Sqlite>>::decode(value)?;
        serde_json::from_value(serde_json::Value::String(text)).map_err(Into::into)
    }
}
impl<'de> Deserialize<'de> for Timestamp {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let value = String::deserialize(deserializer)?;
        if chrono::DateTime::parse_from_rfc3339(&value).is_err()
            && chrono::NaiveDateTime::parse_from_str(&value, "%Y-%m-%dT%H:%M:%S%.f").is_err()
        {
            return Err(serde::de::Error::custom("invalid timestamp"));
        }
        Ok(Self(value))
    }
}
impl Timestamp {
    pub fn now() -> Self {
        Self(chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Micros, true))
    }
    pub fn as_str(&self) -> &str {
        &self.0
    }
}
#[derive(Debug, Clone, Deserialize, Serialize, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum Status {
    Active,
    Completed,
    Aborted,
}
#[derive(Debug, Clone, Deserialize, Serialize, sqlx::Type)]
pub enum Role {
    #[sqlx(rename = "self")]
    #[serde(rename = "self")]
    User,
    #[sqlx(rename = "other")]
    #[serde(rename = "other")]
    Other,
}
#[derive(Debug, Clone, Deserialize, Serialize, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum Format {
    Wav,
    Mp3,
    Ogg,
    Flac,
    Webm,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Meeting {
    pub id: String,
    pub started_at: Timestamp,
    pub status: Status,
    pub ended_at: Option<Timestamp>,
    pub duration_seconds: Option<i64>,
    pub title: Option<String>,
    pub ai_note: String,
    pub minutes: String,
    pub created_at: Option<Timestamp>,
    pub updated_at: Option<Timestamp>,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Turn {
    pub id: String,
    pub meeting_id: String,
    pub sequence: i64,
    pub speaker: String,
    pub text: String,
    pub speaker_id: Option<String>,
    pub created_at: Option<Timestamp>,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Suggestion {
    pub id: String,
    pub meeting_id: String,
    pub target_turn_id: String,
    pub sequence: i64,
    pub agent_id: String,
    pub agent_label: String,
    pub text: String,
    pub created_at: Option<Timestamp>,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Asset {
    pub id: String,
    pub meeting_id: String,
    pub role: Role,
    pub relative_path: String,
    pub format: Format,
    pub sample_rate: i64,
    pub channels: i64,
    pub started_at: Timestamp,
    pub ended_at: Option<Timestamp>,
    pub size_bytes: Option<i64>,
}
#[derive(Debug, Deserialize, Serialize)]
pub struct ListItem {
    pub id: String,
    pub started_at: Timestamp,
    pub status: Status,
    pub ended_at: Option<Timestamp>,
    pub duration_seconds: Option<i64>,
    pub title: Option<String>,
    pub ai_note: String,
    pub created_at: Option<Timestamp>,
    pub updated_at: Option<Timestamp>,
    pub has_recording: bool,
}
#[derive(Deserialize, Serialize)]
pub struct StorageUsage {
    pub meeting: Meeting,
    pub recording_size_bytes: i64,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    CreateMeeting {
        record: Meeting,
    },
    CompleteMeeting {
        meeting_id: String,
        ended_at: Timestamp,
        duration_seconds: Option<i64>,
        ai_note: String,
    },
    AbortMeeting {
        meeting_id: String,
        ended_at: Timestamp,
    },
    GetMeeting {
        meeting_id: String,
    },
    ListMeetings {
        limit: u32,
        offset: u32,
    },
    CountMeetings {},
    UpdateMeetingTitle {
        meeting_id: String,
        title: String,
    },
    UpdateMeetingMinutes {
        meeting_id: String,
        minutes: String,
    },
    DeleteMeeting {
        meeting_id: String,
    },
    ListCompletedMeetingStorageOldest {},
    InsertTurn {
        record: Turn,
    },
    ListTurns {
        meeting_id: String,
    },
    InsertReplySuggestion {
        record: Suggestion,
    },
    ListReplySuggestions {
        meeting_id: String,
    },
    InsertRecordingAssets {
        records: Vec<Asset>,
    },
    ListRecordingAssets {
        meeting_id: String,
    },
    GetRecordingAssetByRole {
        meeting_id: String,
        role: Role,
    },
}
