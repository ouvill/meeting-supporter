use super::{
    catalog::REAZON_FILES,
    check_cancel,
    transfer::{self, FileSpec, Hash},
    valid_model, Key, Manager, ModelError, Phase, Progress,
};
use serde::Deserialize;
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
};
use tokio::sync::watch;
#[derive(Deserialize)]
struct Repository {
    sha: String,
    siblings: Vec<Sibling>,
}
#[derive(Deserialize)]
struct Sibling {
    rfilename: String,
    size: Option<u64>,
    #[serde(rename = "blobId")]
    blob_id: Option<String>,
    lfs: Option<Lfs>,
}
#[derive(Deserialize)]
struct Lfs {
    sha256: String,
    size: u64,
}
fn hex(s: &str, n: usize) -> bool {
    s.len() == n
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn repo_folder(key: Key) -> String {
    format!(
        "models--{}",
        key.repo().unwrap_or_default().replace('/', "--")
    )
}
pub(super) fn cached(cache: &Path, key: Key) -> Option<PathBuf> {
    let root = cache.join(repo_folder(key));
    let revision = if key.revision() != "main" {
        key.revision().to_owned()
    } else {
        std::fs::read_to_string(root.join("refs/main"))
            .ok()?
            .trim()
            .to_owned()
    };
    if !hex(&revision, 40) {
        return None;
    }
    let snapshot = root.join("snapshots").join(revision);
    if !valid_model(&snapshot, key) {
        return None;
    }
    // Standard snapshots may contain symlinks to blobs, but must stay within this repository.
    let canonical_root = std::fs::canonicalize(root).ok()?;
    if !key.required().iter().all(|file| {
        std::fs::canonicalize(snapshot.join(file)).is_ok_and(|p| p.starts_with(&canonical_root))
    }) {
        return None;
    }
    Some(snapshot)
}
fn manifest(bytes: &[u8], key: Key) -> Result<(String, Vec<FileSpec>), ModelError> {
    let repo: Repository = serde_json::from_slice(bytes).map_err(|_| ModelError::Archive)?;
    if !hex(&repo.sha, 40)
        || repo.siblings.len() > 1024
        || (key.revision() != "main" && repo.sha != key.revision())
    {
        return Err(ModelError::Archive);
    }
    let mut files = Vec::new();
    let mut names = HashSet::new();
    let mut total = 0u64;
    for sibling in repo
        .siblings
        .into_iter()
        .filter(|s| key.accepts(&s.rfilename))
    {
        if !names.insert(sibling.rfilename.clone()) {
            return Err(ModelError::Archive);
        }
        let (size, hash) = match sibling.lfs {
            Some(lfs) if hex(&lfs.sha256, 64) && sibling.size.is_none_or(|s| s == lfs.size) => {
                (lfs.size, Hash::Sha256(lfs.sha256))
            }
            None => {
                let digest = sibling.blob_id.ok_or(ModelError::Archive)?;
                if !hex(&digest, 40) {
                    return Err(ModelError::Archive);
                }
                (
                    sibling.size.ok_or(ModelError::Archive)?,
                    Hash::GitBlob(digest),
                )
            }
            _ => return Err(ModelError::Archive),
        };
        total = total.checked_add(size).ok_or(ModelError::Archive)?;
        if size == 0 || total > 8 * 1024 * 1024 * 1024 {
            return Err(ModelError::Archive);
        }
        let pinned = if key == Key::Reazon {
            REAZON_FILES
                .iter()
                .find(|(n, _)| *n == sibling.rfilename)
                .map(|(_, s)| *s)
        } else {
            None
        };
        files.push(FileSpec {
            name: sibling.rfilename,
            size,
            hash,
            pinned,
        });
    }
    if !key.required().iter().all(|name| names.contains(*name)) {
        return Err(ModelError::Archive);
    }
    files.sort_by(|a, b| a.name.cmp(&b.name));
    Ok((repo.sha, files))
}
pub(super) async fn download(
    manager: &Manager,
    key: Key,
    progress: Progress,
    cancel: watch::Receiver<bool>,
) -> Result<PathBuf, ModelError> {
    let client = transfer::client()?;
    let repo = key.repo().ok_or(ModelError::Selection)?;
    let url = format!(
        "{}/api/models/{repo}/revision/{}?blobs=true",
        manager.source.endpoint,
        key.revision()
    );
    let bytes = transfer::metadata(&client, &url, cancel.clone()).await?;
    let (revision, files) = manifest(&bytes, key)?;
    let total = files.iter().map(|f| f.size).sum();
    let mut complete = 0;
    let folder = repo_folder(key);
    let blobs = transfer::directory(&manager.cache, Path::new(&folder).join("blobs").as_path())?;
    let locks = transfer::directory(&manager.cache, Path::new(".locks").join(&folder).as_path())?;
    let snapshot = transfer::directory(
        &manager.cache,
        Path::new(&folder)
            .join("snapshots")
            .join(&revision)
            .as_path(),
    )?;
    for file in files {
        check_cancel(&cancel)?;
        let blob = blobs.join(file.hash.text());
        let _lock = transfer::lock(
            &locks.join(format!("{}.lock", file.hash.text())),
            cancel.clone(),
        )
        .await?;
        if std::fs::symlink_metadata(&blob).is_ok_and(|m| m.file_type().is_symlink()) {
            return Err(ModelError::Archive);
        }
        progress.update(Phase::Verifying, complete, Some(total));
        if !transfer::verified(&blob, file.size, &file.hash, file.pinned, cancel.clone()).await? {
            let url = format!(
                "{}/{repo}/resolve/{revision}/{}",
                manager.source.endpoint, file.name
            );
            transfer::download_file(
                &client,
                &url,
                &blob,
                &file,
                &progress,
                (complete, total),
                cancel.clone(),
            )
            .await?;
        }
        check_cancel(&cancel)?;
        pointer(&snapshot, &file.name, &blob)?;
        complete += file.size;
        progress.update(Phase::Downloading, complete, Some(total));
    }
    check_cancel(&cancel)?;
    if key.revision() == "main" {
        let refs = transfer::directory(&manager.cache, Path::new(&folder).join("refs").as_path())?;
        transfer::atomic_text(&refs.join("main"), &revision)?;
    }
    Ok(snapshot)
}
fn pointer(snapshot: &Path, name: &str, blob: &Path) -> Result<(), ModelError> {
    let destination = snapshot.join(name);
    #[cfg(unix)]
    {
        let temp = tempfile::tempdir_in(snapshot)?;
        let link = temp.path().join("pointer");
        std::os::unix::fs::symlink(
            Path::new("../../blobs").join(blob.file_name().ok_or(ModelError::Archive)?),
            &link,
        )?;
        std::fs::rename(&link, &destination)?;
    }
    #[cfg(not(unix))]
    {
        let temporary = tempfile::NamedTempFile::new_in(snapshot)?;
        std::fs::copy(blob, temporary.path())?;
        temporary
            .persist(destination)
            .map_err(|e| ModelError::Io(e.error))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::super::catalog::Whisper;
    use super::*;
    use serde_json::json;
    #[test]
    fn manifests_reject_incomplete_duplicate_oversized_and_invalid_content_ids() {
        let key = Key::Whisper(Whisper::Tiny);
        let valid = json!({"sha":key.revision(), "siblings":key.required().iter().map(|name| json!({"rfilename":name,"size":10,"blobId":"b".repeat(40)})).collect::<Vec<_>>()});
        assert!(manifest(&serde_json::to_vec(&valid).unwrap(), key).is_ok());
        for variant in 0..5 {
            let mut value = valid.clone();
            match variant {
                0 => value["sha"] = json!("../../outside"),
                1 => value["siblings"][0]["rfilename"] = json!("../config.json"),
                2 => value["siblings"][0]["size"] = json!(9u64 * 1024 * 1024 * 1024),
                3 => value["siblings"][0]["blobId"] = json!("../invalid"),
                _ => {
                    let file = value["siblings"][0].clone();
                    value["siblings"].as_array_mut().unwrap().push(file);
                }
            }
            assert!(matches!(
                manifest(&serde_json::to_vec(&value).unwrap(), key),
                Err(ModelError::Archive)
            ));
        }
    }
}
