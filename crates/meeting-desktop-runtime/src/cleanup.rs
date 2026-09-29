//! Explicit cleanup of completed meetings. Previewing or saving settings never deletes data.
use crate::{runtime::Shared, Error};
use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};
use meeting_storage::{models as db, Repository};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
};

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Request {
    pub cutoff_date: Option<String>,
    pub max_total_bytes: Option<u64>,
}
#[derive(Clone, PartialEq, Eq)]
struct Policy {
    cutoff: Option<NaiveDate>,
    capacity: Option<u64>,
}
impl Request {
    fn policy(&self) -> Result<Policy, Error> {
        let cutoff = self
            .cutoff_date
            .as_ref()
            .map(|s| {
                let date =
                    NaiveDate::parse_from_str(s, "%Y-%m-%d").map_err(|_| Error::CleanupPolicy)?;
                if date.format("%Y-%m-%d").to_string() != *s {
                    return Err(Error::CleanupPolicy);
                }
                Ok(date)
            })
            .transpose()?;
        let capacity = self.max_total_bytes.filter(|v| *v > 0);
        if cutoff.is_none() && capacity.is_none() {
            return Err(Error::CleanupPolicy);
        }
        Ok(Policy { cutoff, capacity })
    }
}
#[derive(Clone, PartialEq, Eq, Serialize)]
pub(crate) struct Preview {
    pub candidate_meeting_ids: Vec<String>,
    pub delete_count: usize,
    pub delete_recording_bytes: u64,
    pub total_recording_bytes_before: u64,
    pub total_recording_bytes_after: u64,
}
#[derive(Clone)]
pub(crate) struct Plan {
    policy: Policy,
    pub preview: Preview,
}
#[derive(Serialize)]
pub(crate) struct Execution {
    #[serde(flatten)]
    pub preview: Preview,
    pub deleted_meeting_ids: Vec<String>,
    pub failed_meeting_ids: Vec<String>,
    pub skipped_meeting_ids: Vec<String>,
}
fn utc(timestamp: &db::Timestamp) -> Result<DateTime<Utc>, Error> {
    DateTime::parse_from_rfc3339(timestamp.as_str())
        .map(|d| d.with_timezone(&Utc))
        .or_else(|_| {
            NaiveDateTime::parse_from_str(timestamp.as_str(), "%Y-%m-%dT%H:%M:%S%.f")
                .map(|d| d.and_utc())
        })
        .map_err(|_| Error::CleanupPolicy)
}
pub(crate) async fn plan(repository: &Repository, request: &Request) -> Result<Plan, Error> {
    let policy = request.policy()?;
    let records: Vec<db::StorageUsage> = serde_json::from_value(
        repository
            .execute(db::Command::ListCompletedMeetingStorageOldest {})
            .await?,
    )?;
    let mut ordered = Vec::with_capacity(records.len());
    let mut total = 0u64;
    for record in records {
        let bytes = u64::try_from(record.recording_size_bytes).map_err(|_| Error::CleanupPolicy)?;
        total = total.checked_add(bytes).ok_or(Error::CleanupPolicy)?;
        ordered.push((
            record.meeting.ended_at.as_ref().map(utc).transpose()?,
            utc(&record.meeting.started_at)?,
            record.meeting.id,
            bytes,
        ));
    }
    ordered.sort();
    let mut ids = Vec::new();
    let mut selected = HashSet::new();
    let mut after = total;
    if let Some(cutoff) = policy.cutoff {
        for (end, _, id, size) in &ordered {
            if end.is_some_and(|date| date.date_naive() < cutoff) {
                ids.push(id.clone());
                selected.insert(id.clone());
                after -= size;
            }
        }
    }
    if let Some(capacity) = policy.capacity {
        for (_, _, id, size) in &ordered {
            if after <= capacity {
                break;
            }
            if *size > 0 && selected.insert(id.clone()) {
                ids.push(id.clone());
                after -= size;
            }
        }
    }
    Ok(Plan {
        policy,
        preview: Preview {
            delete_count: ids.len(),
            candidate_meeting_ids: ids,
            delete_recording_bytes: total - after,
            total_recording_bytes_before: total,
            total_recording_bytes_after: after,
        },
    })
}
pub(crate) async fn execute(
    shared: &Shared,
    request: &Request,
    previous: Plan,
) -> Result<Execution, Error> {
    let current = plan(&shared.repository, request).await?;
    // A newly completed meeting must not silently enter an already confirmed deletion.
    if previous.policy != current.policy || previous.preview != current.preview {
        return Err(Error::CleanupChanged);
    }
    let mut result = Execution {
        preview: current.preview,
        deleted_meeting_ids: vec![],
        failed_meeting_ids: vec![],
        skipped_meeting_ids: vec![],
    };
    for id in &result.preview.candidate_meeting_ids {
        let meeting = shared
            .repository
            .execute(db::Command::GetMeeting {
                meeting_id: id.clone(),
            })
            .await;
        match meeting {
            Ok(m) if m["status"] == "completed" => {}
            Ok(_) => {
                result.skipped_meeting_ids.push(id.clone());
                continue;
            }
            Err(_) => {
                result.failed_meeting_ids.push(id.clone());
                continue;
            }
        }
        let root = shared.config.data_dir.clone();
        let owned_id = id.clone();
        let deleted = tokio::task::spawn_blocking(move || remove_files(&root, &owned_id))
            .await
            .map_err(|_| Error::CleanupFiles)?;
        if deleted.is_err() {
            result.failed_meeting_ids.push(id.clone());
            continue;
        }
        match shared
            .repository
            .execute(db::Command::DeleteMeeting {
                meeting_id: id.clone(),
            })
            .await
        {
            Ok(_) => result.deleted_meeting_ids.push(id.clone()),
            Err(_) => result.failed_meeting_ids.push(id.clone()),
        }
    }
    Ok(result)
}
fn directory(root: &Path, family: &str, id: &str) -> Result<Option<PathBuf>, Error> {
    let parent = root.join(family);
    let path = parent.join(id);
    for candidate in [&parent, &path] {
        match std::fs::symlink_metadata(candidate) {
            Ok(meta) if !meta.file_type().is_symlink() && meta.is_dir() => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            _ => return Err(Error::CleanupFiles),
        }
    }
    let base = std::fs::canonicalize(root).map_err(|_| Error::CleanupFiles)?;
    let resolved = std::fs::canonicalize(&path).map_err(|_| Error::CleanupFiles)?;
    if resolved != base.join(family).join(id) {
        return Err(Error::CleanupFiles);
    }
    Ok(Some(path))
}
pub(crate) fn remove_files(root: &Path, id: &str) -> Result<(), Error> {
    if id.is_empty()
        || id.len() > 128
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
    {
        return Err(Error::CleanupFiles);
    }
    // Preflight both families before touching either one. remove_dir_all unlinks child symlinks.
    let recordings = directory(root, "recordings", id)?;
    let materials = directory(root, "meetings", id)?;
    for path in [recordings, materials].into_iter().flatten() {
        std::fs::remove_dir_all(path).map_err(|_| Error::CleanupFiles)?;
    }
    Ok(())
}
