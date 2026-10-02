//! Reconcile stopped meetings without resuming them or modifying recorded audio.
use crate::Error;
use meeting_storage::{models as db, Repository};
use std::{io::Read, path::Path};

pub(crate) async fn reconcile(repository: &Repository, root: &Path) -> Result<(), Error> {
    let ids: Vec<String> = serde_json::from_value(
        repository
            .execute(db::Command::ListActiveMeetingIds {})
            .await?,
    )?;
    for id in ids {
        let meeting: db::Meeting = serde_json::from_value(
            repository
                .execute(db::Command::GetMeeting {
                    meeting_id: id.clone(),
                })
                .await?,
        )?;
        register_recordings(repository, root, &meeting).await?;
        repository
            .execute(db::Command::AbortMeeting {
                meeting_id: id,
                // The time of a crash is unknown; do not use the next startup as its end.
                ended_at: None,
            })
            .await?;
    }
    Ok(())
}

pub(crate) async fn register_recordings(
    repository: &Repository,
    root: &Path,
    meeting: &db::Meeting,
) -> Result<(), Error> {
    let existing: Vec<db::Asset> = serde_json::from_value(
        repository
            .execute(db::Command::ListRecordingAssets {
                meeting_id: meeting.id.clone(),
            })
            .await?,
    )?;
    let mut assets = Vec::new();
    for (role, name) in [(db::Role::User, "self"), (db::Role::Other, "other")] {
        if existing.iter().any(|a| {
            matches!(
                (&a.role, &role),
                (db::Role::User, db::Role::User) | (db::Role::Other, db::Role::Other)
            )
        }) {
            continue;
        }
        let root = root.to_path_buf();
        let id = meeting.id.clone();
        let bytes = tokio::task::spawn_blocking(move || usable_wav(&root, &id, name))
            .await
            .map_err(|_| Error::Closed)?;
        if let Some(size_bytes) = bytes {
            assets.push(db::Asset {
                id: uuid::Uuid::new_v4().to_string(),
                meeting_id: meeting.id.clone(),
                role,
                relative_path: format!("recordings/{}/{name}.wav", meeting.id),
                format: db::Format::Wav,
                sample_rate: 16000,
                channels: 1,
                started_at: meeting.started_at.clone(),
                ended_at: None,
                size_bytes: Some(size_bytes),
            });
        }
    }
    if !assets.is_empty() {
        repository
            .execute(db::Command::InsertRecordingAssets { records: assets })
            .await?;
    }
    Ok(())
}

// Only publish complete, app-format WAVs. Incomplete or unsafe files remain on
// disk for explicit deletion; never guess their samples or repair them in place.
fn usable_wav(root: &Path, id: &str, role: &str) -> Option<i64> {
    if id.is_empty()
        || id.len() > 128
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
    {
        return None;
    }
    let parent = root.join("recordings");
    let directory = parent.join(id);
    let path = directory.join(format!("{role}.wav"));
    for candidate in [&parent, &directory] {
        if !std::fs::symlink_metadata(candidate).ok()?.is_dir() {
            return None;
        }
    }
    // Reject links, FIFOs and devices before opening; a special file could block startup.
    if !std::fs::symlink_metadata(&path).ok()?.is_file() {
        return None;
    }
    let mut file = std::fs::File::open(path).ok()?;
    let metadata = file.metadata().ok()?;
    if !metadata.is_file() {
        return None;
    }
    let mut header = [0u8; 8];
    file.read_exact(&mut header).ok()?;
    if &header[..4] != b"RIFF"
        || u64::from(u32::from_le_bytes(header[4..].try_into().ok()?)) + 8 != metadata.len()
    {
        return None;
    }
    use std::io::{Seek, SeekFrom};
    file.seek(SeekFrom::Start(0)).ok()?;
    let reader = hound::WavReader::new(file).ok()?;
    let spec = reader.spec();
    if spec.channels != 1
        || spec.sample_rate != 16000
        || spec.bits_per_sample != 16
        || spec.sample_format != hound::SampleFormat::Int
        || reader.duration() == 0
        || u64::from(reader.duration()) * 2 + 44 > metadata.len()
    {
        return None;
    }
    i64::try_from(metadata.len()).ok()
}
