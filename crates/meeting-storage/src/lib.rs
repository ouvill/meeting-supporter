//! Sole SQLite owner. All application queries are verified by SQLx at compile time.
pub mod models;
use models::*;
use serde::Serialize;
use serde_json::{json, Value};
use sqlx::{
    sqlite::{SqliteConnectOptions, SqlitePoolOptions},
    Executor, SqlitePool,
};
use std::{path::Path, time::Duration};
use thiserror::Error;

#[derive(Debug, Error, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StorageError {
    #[error("storage_unavailable")]
    Unavailable,
    #[error("storage_constraint")]
    Constraint,
    #[error("storage_invalid_data")]
    InvalidData,
    #[error("storage_unsupported_schema")]
    UnsupportedSchema,
}
impl From<sqlx::Error> for StorageError {
    fn from(error: sqlx::Error) -> Self {
        match error {
            sqlx::Error::Database(ref error)
                if error.is_unique_violation()
                    || error.is_foreign_key_violation()
                    || error.is_check_violation() =>
            {
                Self::Constraint
            }
            sqlx::Error::ColumnDecode { .. } | sqlx::Error::Decode(_) => Self::InvalidData,
            _ => Self::Unavailable,
        }
    }
}
fn encode(value: impl Serialize) -> Result<Value, StorageError> {
    serde_json::to_value(value).map_err(|_| StorageError::InvalidData)
}

/// Opening validates/migrates the schema before exposing any operations.
pub struct Repository {
    pool: SqlitePool,
}
impl Repository {
    pub async fn open(path: &Path) -> Result<Self, StorageError> {
        let options = SqliteConnectOptions::new()
            .filename(path)
            .create_if_missing(true)
            .foreign_keys(true)
            .busy_timeout(Duration::from_secs(5));
        let pool = SqlitePoolOptions::new()
            .max_connections(1)
            .connect_with(options)
            .await?;
        let result = Self::migrate(&pool).await;
        if let Err(error) = result {
            pool.close().await;
            return Err(error);
        }
        Ok(Self { pool })
    }
    async fn migrate(pool: &SqlitePool) -> Result<(), StorageError> {
        let exists = sqlx::query_scalar!(
r#"SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version') AS "exists!: i64""#
).fetch_one(pool).await?;
        let version = if exists != 0 {
            sqlx::query_scalar!(
                r#"SELECT COALESCE(MAX(version),0) AS "version!: i64" FROM schema_version"#
            )
            .fetch_one(pool)
            .await?
        } else {
            0
        };
        if !(0..=2).contains(&version) {
            return Err(StorageError::UnsupportedSchema);
        }
        // Connection configuration and schema DDL are not application data queries.
        pool.execute("PRAGMA journal_mode=WAL").await?;
        let mut transaction = pool.begin().await?;
        // DDL bootstraps the same schema used by build.rs; data queries below use macros.
        (&mut *transaction)
            .execute(include_str!("schema.sql"))
            .await?;
        if version == 1 {
            (&mut *transaction)
                .execute("ALTER TABLE meetings ADD COLUMN minutes TEXT NOT NULL DEFAULT ''")
                .await?;
        }
        if version < 2 {
            sqlx::query!("DELETE FROM schema_version")
                .execute(&mut *transaction)
                .await?;
            let applied_at = Timestamp::now();
            let applied_at = applied_at.as_str();
            sqlx::query!(
                "INSERT INTO schema_version(version,applied_at)
                     VALUES(2,?)",
                applied_at,
            )
            .execute(&mut *transaction)
            .await?;
        }
        transaction.commit().await?;
        Ok(())
    }
    pub async fn close(self) {
        self.pool.close().await;
    }
    pub async fn execute(&self, command: Command) -> Result<Value, StorageError> {
        let now = Timestamp::now();
        match command {
            Command::CreateMeeting { record: m } => {
                let started_at = m.started_at.as_str();
                let ended_at = m.ended_at.as_ref().map(Timestamp::as_str);
                let created_at = m.created_at.as_ref().unwrap_or(&now).as_str();
                let updated_at = m.updated_at.as_ref().unwrap_or(&now).as_str();
                sqlx::query!(
                    "INSERT INTO meetings(id,started_at,status,ended_at,duration_seconds,title,ai_note,minutes,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?)",
                    m.id,
                    started_at,
                    m.status,
                    ended_at,
                    m.duration_seconds,
                    m.title,
                    m.ai_note,
                    m.minutes,
                    created_at,
                    updated_at,
                ).execute(&self.pool).await?;
                Ok(Value::Null)
            }
            Command::CompleteMeeting {
                meeting_id,
                ended_at,
                duration_seconds,
                ai_note,
            } => {
                let ended_at_value = ended_at.as_str();
                let updated_at = now.as_str();
                sqlx::query!(
                    "UPDATE meetings SET status='completed',ended_at=?,duration_seconds=?,ai_note=?,updated_at=?
                     WHERE id=?",
                    ended_at_value,
                    duration_seconds,
                    ai_note,
                    updated_at,
                    meeting_id,
                ).execute(&self.pool).await?;
                Ok(Value::Null)
            }
            Command::AbortMeeting {
                meeting_id,
                ended_at,
            } => {
                let ended_at_value = ended_at.as_str();
                let updated_at = now.as_str();
                sqlx::query!(
                    "UPDATE meetings SET status='aborted',ended_at=?,updated_at=?
                     WHERE id=?",
                    ended_at_value,
                    updated_at,
                    meeting_id,
                )
                .execute(&self.pool)
                .await?;
                Ok(Value::Null)
            }
            Command::UpdateMeetingTitle { meeting_id, title } => {
                let updated_at = now.as_str();
                let result = sqlx::query!(
                    "UPDATE meetings SET title=?,updated_at=?
                     WHERE id=?",
                    title,
                    updated_at,
                    meeting_id,
                )
                .execute(&self.pool)
                .await?;
                encode(result.rows_affected())
            }
            Command::UpdateMeetingMinutes {
                meeting_id,
                minutes,
            } => {
                let updated_at = now.as_str();
                let result = sqlx::query!(
                    "UPDATE meetings SET minutes=?,updated_at=?
                     WHERE id=?",
                    minutes,
                    updated_at,
                    meeting_id,
                )
                .execute(&self.pool)
                .await?;
                encode(result.rows_affected())
            }
            Command::DeleteMeeting { meeting_id } => {
                sqlx::query!(
                    "DELETE FROM meetings
                     WHERE id=?",
                    meeting_id,
                )
                    .execute(&self.pool)
                    .await?;
                Ok(Value::Null)
            }
            Command::InsertTurn { record: t } => {
                let created_at = t.created_at.as_ref().unwrap_or(&now).as_str();
                sqlx::query!(
                    "INSERT INTO meeting_turns(id,meeting_id,sequence,speaker,text,speaker_id,created_at)
                     VALUES(?,?,?,?,?,?,?)",
                    t.id,
                    t.meeting_id,
                    t.sequence,
                    t.speaker,
                    t.text,
                    t.speaker_id,
                    created_at,
                ).execute(&self.pool).await?;
                Ok(Value::Null)
            }
            Command::InsertReplySuggestion { record: s } => {
                let created_at = s.created_at.as_ref().unwrap_or(&now).as_str();
                sqlx::query!(
                    "INSERT INTO reply_suggestions(id,meeting_id,target_turn_id,sequence,agent_id,agent_label,text,created_at)
                     VALUES(?,?,?,?,?,?,?,?)",
                    s.id,
                    s.meeting_id,
                    s.target_turn_id,
                    s.sequence,
                    s.agent_id,
                    s.agent_label,
                    s.text,
                    created_at,
                ).execute(&self.pool).await?;
                Ok(Value::Null)
            }
            Command::GetMeeting { meeting_id } => encode(
                sqlx::query_as!(
                    Meeting,
                    r#"SELECT id AS "id!: String",
                        started_at AS "started_at!: Timestamp",
                        status AS "status!: Status",
                        ended_at AS "ended_at?: Timestamp",
                        duration_seconds AS "duration_seconds?: i64",
                        title AS "title?: String",
                        ai_note AS "ai_note!: String",
                        minutes AS "minutes!: String",
                        created_at AS "created_at?: Timestamp",
                        updated_at AS "updated_at?: Timestamp"
                    FROM meetings
                    WHERE id=?"#,
                    meeting_id
                )
                .fetch_optional(&self.pool)
                .await?,
            ),
            Command::ListMeetings { limit, offset } => encode(
                sqlx::query_as!(
                    ListItem,
                    r#"SELECT id AS "id!: String",
                        started_at AS "started_at!: Timestamp",
                        status AS "status!: Status",
                        ended_at AS "ended_at?: Timestamp",
                        duration_seconds AS "duration_seconds?: i64",
                        title AS "title?: String",
                        ai_note AS "ai_note!: String",
                        created_at AS "created_at?: Timestamp",
                        updated_at AS "updated_at?: Timestamp",
                        EXISTS(SELECT 1 FROM recording_assets WHERE meeting_id=meetings.id) AS "has_recording!: bool"
                    FROM meetings
                    ORDER BY started_at DESC,id DESC
                    LIMIT ? OFFSET ?"#,
                    limit,
                    offset
                )
                .fetch_all(&self.pool)
                .await?,
            ),
            Command::ListTurns { meeting_id } => encode(
                sqlx::query_as!(
                    Turn,
                    r#"SELECT id AS "id!: String",
                        meeting_id AS "meeting_id!: String",
                        sequence AS "sequence!: i64",
                        speaker AS "speaker!: String",
                        text AS "text!: String",
                        speaker_id AS "speaker_id?: String",
                        created_at AS "created_at?: Timestamp"
                    FROM meeting_turns
                    WHERE meeting_id=?
                    ORDER BY sequence"#,
                    meeting_id
                )
                .fetch_all(&self.pool)
                .await?,
            ),
            Command::ListReplySuggestions { meeting_id } => encode(
                sqlx::query_as!(
                    Suggestion,
                    r#"SELECT id AS "id!: String",
                        meeting_id AS "meeting_id!: String",
                        target_turn_id AS "target_turn_id!: String",
                        sequence AS "sequence!: i64",
                        agent_id AS "agent_id!: String",
                        agent_label AS "agent_label!: String",
                        text AS "text!: String",
                        created_at AS "created_at?: Timestamp"
                    FROM reply_suggestions
                    WHERE meeting_id=?
                    ORDER BY sequence"#,
                    meeting_id
                )
                .fetch_all(&self.pool)
                .await?,
            ),
            Command::ListRecordingAssets { meeting_id } => encode(
                sqlx::query_as!(
                    Asset,
                    r#"SELECT id AS "id!: String",
                        meeting_id AS "meeting_id!: String",
                        role AS "role!: Role",
                        relative_path AS "relative_path!: String",
                        format AS "format!: Format",
                        sample_rate AS "sample_rate!: i64",
                        channels AS "channels!: i64",
                        started_at AS "started_at!: Timestamp",
                        ended_at AS "ended_at?: Timestamp",
                        size_bytes AS "size_bytes?: i64"
                    FROM recording_assets
                    WHERE meeting_id=?"#,
                    meeting_id
                )
                .fetch_all(&self.pool)
                .await?,
            ),
            Command::GetRecordingAssetByRole { meeting_id, role } => encode(
                sqlx::query_as!(
                    Asset,
                    r#"SELECT id AS "id!: String",
                        meeting_id AS "meeting_id!: String",
                        role AS "role!: Role",
                        relative_path AS "relative_path!: String",
                        format AS "format!: Format",
                        sample_rate AS "sample_rate!: i64",
                        channels AS "channels!: i64",
                        started_at AS "started_at!: Timestamp",
                        ended_at AS "ended_at?: Timestamp",
                        size_bytes AS "size_bytes?: i64"
                    FROM recording_assets
                    WHERE meeting_id=? AND role=?"#,
                    meeting_id,
                    role
                )
                .fetch_optional(&self.pool)
                .await?,
            ),
            Command::CountMeetings {} => Ok(json!(
                sqlx::query_scalar!(r#"SELECT COUNT(*) AS "count!: i64" FROM meetings"#)
                    .fetch_one(&self.pool)
                    .await?
            )),
            Command::InsertRecordingAssets { records } => {
                let mut transaction = self.pool.begin().await?;
                for a in records {
                    let started_at = a.started_at.as_str();
                    let ended_at = a.ended_at.as_ref().map(Timestamp::as_str);
                    sqlx::query!(
                    "INSERT INTO recording_assets(id,meeting_id,role,relative_path,format,sample_rate,channels,started_at,ended_at,size_bytes)
                     VALUES(?,?,?,?,?,?,?,?,?,?)",
                    a.id,
                    a.meeting_id,
                    a.role,
                    a.relative_path,
                    a.format,
                    a.sample_rate,
                    a.channels,
                    started_at,
                    ended_at,
                    a.size_bytes,
                ).execute(&mut *transaction).await?;
                }
                transaction.commit().await?;
                Ok(Value::Null)
            }
            Command::ListCompletedMeetingStorageOldest {} => {
                let mut transaction = self.pool.begin().await?;
                let meetings = sqlx::query_as!(
                    Meeting,
                    r#"SELECT id AS "id!: String",
                        started_at AS "started_at!: Timestamp",
                        status AS "status!: Status",
                        ended_at AS "ended_at?: Timestamp",
                        duration_seconds AS "duration_seconds?: i64",
                        title AS "title?: String",
                        ai_note AS "ai_note!: String",
                        minutes AS "minutes!: String",
                        created_at AS "created_at?: Timestamp",
                        updated_at AS "updated_at?: Timestamp"
                    FROM meetings
                    WHERE status='completed'
                    ORDER BY ended_at ASC,started_at ASC,id ASC"#
                )
                .fetch_all(&mut *transaction)
                .await?;
                let sizes = sqlx::query!(
                    r#"SELECT meeting_id AS "meeting_id!: String",
                        COALESCE(SUM(size_bytes),0) AS "bytes!: i64"
                    FROM recording_assets GROUP BY meeting_id"#
                )
                .fetch_all(&mut *transaction)
                .await?;
                transaction.commit().await?;
                let sizes: std::collections::HashMap<_, _> =
                    sizes.into_iter().map(|r| (r.meeting_id, r.bytes)).collect();
                encode(
                    meetings
                        .into_iter()
                        .map(|meeting| StorageUsage {
                            recording_size_bytes: sizes.get(&meeting.id).copied().unwrap_or(0),
                            meeting,
                        })
                        .collect::<Vec<_>>(),
                )
            }
        }
    }
}
