use super::{check_cancel, ModelError, Phase, Progress};
use fs2::FileExt;
use sha2::{Digest, Sha256};
use std::{
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    sync::watch,
};

pub(super) struct Source {
    pub endpoint: String,
}
impl Default for Source {
    fn default() -> Self {
        Self {
            endpoint: "https://huggingface.co".into(),
        }
    }
}
pub(super) fn client() -> Result<reqwest::Client, ModelError> {
    reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(15))
        .read_timeout(Duration::from_secs(30))
        .user_agent("meeting-supporter/0.1.0")
        .build()
        .map_err(|_| ModelError::Network)
}
pub(super) async fn cancelled<T>(
    mut cancel: watch::Receiver<bool>,
    future: impl std::future::Future<Output = Result<T, ModelError>>,
) -> Result<T, ModelError> {
    check_cancel(&cancel)?;
    tokio::select! {result=future=>result,_=cancel.wait_for(|v|*v)=>Err(ModelError::Cancelled)}
}
pub(super) async fn metadata(
    client: &reqwest::Client,
    url: &str,
    cancel: watch::Receiver<bool>,
) -> Result<Vec<u8>, ModelError> {
    cancelled(cancel, async {
        let mut response = client
            .get(url)
            .send()
            .await
            .map_err(|_| ModelError::Network)?
            .error_for_status()
            .map_err(|_| ModelError::Network)?;
        let mut bytes = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|_| ModelError::Network)? {
            if bytes.len() + chunk.len() > 2 * 1024 * 1024 {
                return Err(ModelError::Archive);
            }
            bytes.extend_from_slice(&chunk);
        }
        Ok(bytes)
    })
    .await
}
/// Python's FileLock uses this same flock path. Never remove shared lock files on release.
pub(super) async fn lock(
    path: &Path,
    cancel: watch::Receiver<bool>,
) -> Result<std::fs::File, ModelError> {
    let file = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(path)?;
    cancelled(cancel, async {
        loop {
            match file.try_lock_exclusive() {
                Ok(()) => return Ok(file),
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    tokio::time::sleep(Duration::from_millis(50)).await
                }
                Err(e) => return Err(e.into()),
            }
        }
    })
    .await
}
#[derive(Clone)]
pub(super) enum Hash {
    Sha256(String),
    GitBlob(String),
}
impl Hash {
    pub fn text(&self) -> &str {
        match self {
            Self::Sha256(s) | Self::GitBlob(s) => s,
        }
    }
}
enum Hasher {
    Sha256(Sha256),
    GitBlob(sha1::Sha1),
}
impl Hasher {
    fn new(hash: &Hash, size: u64) -> Self {
        match hash {
            Hash::Sha256(_) => Self::Sha256(Sha256::new()),
            Hash::GitBlob(_) => {
                let mut h = sha1::Sha1::new();
                h.update(format!("blob {size}\0").as_bytes());
                Self::GitBlob(h)
            }
        }
    }
    fn update(&mut self, bytes: &[u8]) {
        match self {
            Self::Sha256(h) => h.update(bytes),
            Self::GitBlob(h) => h.update(bytes),
        }
    }
    fn matches(self, hash: &Hash) -> bool {
        let actual = match self {
            Self::Sha256(h) => format!("{:x}", h.finalize()),
            Self::GitBlob(h) => format!("{:x}", h.finalize()),
        };
        actual == hash.text()
    }
}
pub(super) async fn verified(
    path: &Path,
    size: u64,
    hash: &Hash,
    pinned: Option<&str>,
    cancel: watch::Receiver<bool>,
) -> Result<bool, ModelError> {
    cancelled(cancel, async {
        let mut input = match tokio::fs::File::open(path).await {
            Ok(f) => f,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(false),
            Err(e) => return Err(e.into()),
        };
        if input.metadata().await?.len() != size {
            return Ok(false);
        }
        let mut digest = Hasher::new(hash, size);
        let mut sha = Sha256::new();
        let mut buf = vec![0; 256 * 1024];
        loop {
            let n = input.read(&mut buf).await?;
            if n == 0 {
                break;
            }
            digest.update(&buf[..n]);
            if pinned.is_some() {
                sha.update(&buf[..n]);
            }
        }
        Ok(digest.matches(hash)
            && pinned.is_none_or(|expected| format!("{:x}", sha.finalize()) == expected))
    })
    .await
}
pub(super) struct FileSpec {
    pub name: String,
    pub size: u64,
    pub hash: Hash,
    pub pinned: Option<&'static str>,
}
pub(super) async fn download_file(
    client: &reqwest::Client,
    url: &str,
    blob: &Path,
    spec: &FileSpec,
    progress: &Progress,
    counts: (u64, u64),
    cancel: watch::Receiver<bool>,
) -> Result<(), ModelError> {
    let (base, total) = counts;
    // TempPath remains owned outside the cancellable future, after the async file is dropped.
    let temporary = tempfile::NamedTempFile::new_in(blob.parent().ok_or(ModelError::Archive)?)?;
    cancelled(cancel, async {
        let mut output = tokio::fs::File::from_std(temporary.reopen()?);
        let mut response = client
            .get(url)
            .send()
            .await
            .map_err(|_| ModelError::Network)?
            .error_for_status()
            .map_err(|_| ModelError::Network)?;
        if response.content_length().is_some_and(|n| n != spec.size) {
            return Err(ModelError::Checksum);
        }
        let mut digest = Hasher::new(&spec.hash, spec.size);
        let mut sha = Sha256::new();
        let mut written = 0u64;
        while let Some(chunk) = response.chunk().await.map_err(|_| ModelError::Network)? {
            written = written
                .checked_add(chunk.len() as u64)
                .ok_or(ModelError::Archive)?;
            if written > spec.size {
                return Err(ModelError::Checksum);
            }
            output.write_all(&chunk).await?;
            digest.update(&chunk);
            if spec.pinned.is_some() {
                sha.update(&chunk);
            }
            progress.update(Phase::Downloading, base + written, Some(total));
        }
        progress.update(Phase::Verifying, base + written, Some(total));
        if written != spec.size
            || !digest.matches(&spec.hash)
            || spec
                .pinned
                .is_some_and(|s| format!("{:x}", sha.finalize()) != s)
        {
            return Err(ModelError::Checksum);
        }
        output.flush().await?;
        output.sync_all().await?;
        drop(output);
        // Commit only a fully verified blob. Existing snapshots keep pointing to the same blob name.
        temporary
            .persist(blob)
            .map_err(|e| ModelError::Io(e.error))?;
        Ok(())
    })
    .await
}
pub(super) fn directory(root: &Path, relative: &Path) -> Result<PathBuf, ModelError> {
    std::fs::create_dir_all(root)?;
    let mut current = root.to_path_buf();
    for component in relative.components() {
        let std::path::Component::Normal(name) = component else {
            return Err(ModelError::Archive);
        };
        current.push(name);
        match std::fs::create_dir(&current) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(e) => return Err(e.into()),
        }
        let meta = std::fs::symlink_metadata(&current)?;
        if meta.file_type().is_symlink() || !meta.is_dir() {
            return Err(ModelError::Archive);
        }
    }
    Ok(current)
}
pub(super) fn atomic_text(path: &Path, contents: &str) -> Result<(), ModelError> {
    use std::io::Write;
    let mut file = tempfile::NamedTempFile::new_in(path.parent().ok_or(ModelError::Archive)?)?;
    file.write_all(contents.as_bytes())?;
    file.as_file().sync_all()?;
    file.persist(path).map_err(|e| ModelError::Io(e.error))?;
    Ok(())
}
