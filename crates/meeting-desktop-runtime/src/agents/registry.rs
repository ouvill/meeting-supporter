//! ACP Registry metadata and app-owned installations bounded by Registry versions.
use super::{npm, AgentError};
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    path::{Component, Path, PathBuf},
    time::Duration,
};
use tokio::io::AsyncWriteExt;

pub const INDEX: &str = "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json";
const MAX_ARCHIVE: u64 = 2 * 1024 * 1024 * 1024;
const MAX_EXPANDED: u64 = 4 * 1024 * 1024 * 1024;

#[derive(Clone, Deserialize, Serialize)]
pub struct Entry {
    pub id: String,
    pub name: String,
    pub version: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub authors: Vec<String>,
    #[serde(default)]
    pub license: String,
    #[serde(default)]
    pub license_url: Option<String>,
    pub distribution: Distribution,
}
#[derive(Clone, Default, Deserialize, Serialize)]
pub struct Distribution {
    #[serde(default)]
    pub binary: BTreeMap<String, Binary>,
    pub npx: Option<Package>,
    pub uvx: Option<Package>,
}
#[derive(Clone, Deserialize, Serialize)]
pub struct Binary {
    pub archive: String,
    pub sha256: Option<String>,
    pub cmd: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
}
#[derive(Clone, Deserialize, Serialize)]
pub struct Package {
    pub package: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
}
#[derive(Clone, Deserialize, Serialize)]
pub struct Installed {
    pub entry: Entry,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub installed_version: Option<String>,
    pub directory: String,
    pub executable: String,
    pub args: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub node: bool,
    /// ACP session model selected by the user. Older manifests omit it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub thought_level: Option<String>,
}
#[derive(Clone)]
pub struct Launch {
    pub id: String,
    pub command: PathBuf,
    pub args: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub model: Option<String>,
    pub thought_level: Option<String>,
}

pub fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 100
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
}
fn version(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 100
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b".-+".contains(&c))
}
fn https(value: &str) -> Result<reqwest::Url, AgentError> {
    let url = reqwest::Url::parse(value).map_err(|_| AgentError::Registry)?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err(AgentError::Registry);
    }
    Ok(url)
}
pub fn relative(value: &str) -> Result<PathBuf, AgentError> {
    let path = Path::new(value);
    if value.is_empty()
        || value.contains('\\')
        || value.contains(':')
        || !path
            .components()
            .all(|c| matches!(c, Component::Normal(_) | Component::CurDir))
        || path.file_name().is_none()
    {
        return Err(AgentError::Registry);
    }
    Ok(path.to_owned())
}
pub fn platform() -> String {
    format!(
        "{}-{}",
        if cfg!(target_os = "macos") {
            "darwin"
        } else {
            std::env::consts::OS
        },
        std::env::consts::ARCH
    )
}
impl Entry {
    pub fn distribution_kind(&self) -> &'static str {
        if self
            .distribution
            .binary
            .get(&platform())
            .is_some_and(|b| binary_format(b).is_ok() && relative(&b.cmd).is_ok())
        {
            "binary"
        } else if self
            .distribution
            .npx
            .as_ref()
            .is_some_and(|p| npm::package_spec(&p.package).is_ok_and(|(_, v)| v == self.version))
        {
            "npm"
        } else {
            "unsupported"
        }
    }
    pub fn supported(&self) -> bool {
        identifier(&self.id) && version(&self.version) && self.distribution_kind() != "unsupported"
    }
}
fn client() -> Result<reqwest::Client, AgentError> {
    reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(15))
        .redirect(reqwest::redirect::Policy::custom(|attempt| {
            if attempt.previous().len() >= 5 || attempt.url().scheme() != "https" {
                attempt.stop()
            } else {
                attempt.follow()
            }
        }))
        .build()
        .map_err(|_| AgentError::Download)
}
pub async fn fetch() -> Result<Vec<Entry>, AgentError> {
    #[derive(Deserialize)]
    struct Index {
        agents: Vec<Entry>,
    }
    let response = client()?
        .get(INDEX)
        .timeout(Duration::from_secs(20))
        .send()
        .await
        .map_err(|_| AgentError::Registry)?
        .error_for_status()
        .map_err(|_| AgentError::Registry)?;
    let mut stream = response.bytes_stream();
    let mut data = Vec::new();
    while let Some(bytes) = stream.next().await {
        let bytes = bytes.map_err(|_| AgentError::Registry)?;
        if data.len() + bytes.len() > 4 * 1024 * 1024 {
            return Err(AgentError::Registry);
        }
        data.extend_from_slice(&bytes);
    }
    let index: Index = serde_json::from_slice(&data).map_err(|_| AgentError::Registry)?;
    let mut seen = std::collections::HashSet::new();
    if index.agents.len() > 1000
        || index
            .agents
            .iter()
            .any(|a| !identifier(&a.id) || !version(&a.version) || !seen.insert(&a.id))
    {
        return Err(AgentError::Registry);
    }
    Ok(index.agents)
}
pub fn load(root: &Path) -> Result<BTreeMap<String, Installed>, AgentError> {
    let path = root.join("installed.json");
    if !path.exists() {
        return Ok(BTreeMap::new());
    }
    let bytes = std::fs::read(path).map_err(|_| AgentError::Storage)?;
    if bytes.len() > 4 * 1024 * 1024 {
        return Err(AgentError::Storage);
    }
    let entries: BTreeMap<String, Installed> =
        serde_json::from_slice(&bytes).map_err(|_| AgentError::Storage)?;
    for (id, entry) in &entries {
        if id != &entry.entry.id || !identifier(id) {
            return Err(AgentError::Storage);
        }
        if !version(entry.version()) {
            return Err(AgentError::Storage);
        }
        relative(&entry.directory)?;
        relative(&entry.executable)?;
    }
    Ok(entries)
}
pub fn save(root: &Path, entries: &BTreeMap<String, Installed>) -> Result<(), AgentError> {
    use std::io::Write;
    std::fs::create_dir_all(root).map_err(|_| AgentError::Storage)?;
    let mut file = tempfile::NamedTempFile::new_in(root).map_err(|_| AgentError::Storage)?;
    file.write_all(&serde_json::to_vec(entries).map_err(|_| AgentError::Storage)?)
        .map_err(|_| AgentError::Storage)?;
    file.as_file().sync_all().map_err(|_| AgentError::Storage)?;
    file.persist(root.join("installed.json"))
        .map_err(|_| AgentError::Storage)?;
    Ok(())
}
impl Installed {
    pub fn version(&self) -> &str {
        // 固定版を導入していた旧形式の保存データもそのまま読める。
        self.installed_version
            .as_deref()
            .unwrap_or(&self.entry.version)
    }

    pub fn launch(&self, root: &Path) -> Result<Launch, AgentError> {
        let executable = root
            .join(relative(&self.directory)?)
            .join(relative(&self.executable)?);
        if !executable.is_file() {
            return Err(AgentError::NotInstalled);
        }
        let mut args = self.args.clone();
        let command = if self.node {
            args.insert(0, executable.to_string_lossy().into_owned());
            PathBuf::from("node")
        } else {
            executable
        };
        Ok(Launch {
            id: self.entry.id.clone(),
            command,
            args,
            env: self.env.clone(),
            model: self.model.clone(),
            thought_level: self.thought_level.clone(),
        })
    }
}
pub async fn install(root: &Path, entry: Entry) -> Result<Installed, AgentError> {
    if !entry.supported() {
        return Err(AgentError::Platform);
    }
    tokio::fs::create_dir_all(root)
        .await
        .map_err(|_| AgentError::Storage)?;
    // Staging is never launched; the manifest only points at a completely installed version.
    let staging = tempfile::Builder::new()
        .prefix(".install-")
        .tempdir_in(root)
        .map_err(|_| AgentError::Storage)?;
    let (executable, args, env, node, installed_version) = if let Some(binary) = entry
        .distribution
        .binary
        .get(&platform())
        .filter(|b| binary_format(b).is_ok() && relative(&b.cmd).is_ok())
    {
        let url = https(&binary.archive)?;
        let archive = staging.path().join("download");
        let response = client()?
            .get(url)
            .timeout(Duration::from_secs(600))
            .send()
            .await
            .map_err(|_| AgentError::Download)?
            .error_for_status()
            .map_err(|_| AgentError::Download)?;
        if response.content_length().is_some_and(|n| n > MAX_ARCHIVE) {
            return Err(AgentError::Download);
        }
        let mut file = tokio::fs::File::create(&archive)
            .await
            .map_err(|_| AgentError::Storage)?;
        let mut stream = response.bytes_stream();
        let mut size = 0;
        use sha2::{Digest, Sha256};
        let mut digest = Sha256::new();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|_| AgentError::Download)?;
            size += chunk.len() as u64;
            if size > MAX_ARCHIVE {
                return Err(AgentError::Download);
            }
            digest.update(&chunk);
            file.write_all(&chunk)
                .await
                .map_err(|_| AgentError::Storage)?;
        }
        file.sync_all().await.map_err(|_| AgentError::Storage)?;
        drop(file);
        if binary
            .sha256
            .as_ref()
            .is_some_and(|hash| !format!("{:x}", digest.finalize()).eq_ignore_ascii_case(hash))
        {
            return Err(AgentError::Integrity);
        }
        let destination = staging.path().join("files");
        let binary_copy = binary.clone();
        let output = destination.clone();
        tokio::task::spawn_blocking(move || extract(&archive, &output, &binary_copy))
            .await
            .map_err(|_| AgentError::Storage)??;
        (
            format!("files/{}", relative(&binary.cmd)?.to_string_lossy()),
            binary.args.clone(),
            binary.env.clone(),
            false,
            entry.version.clone(),
        )
    } else if let Some(package) = &entry.distribution.npx {
        let (_, package_version) = npm::package_spec(&package.package)?;
        if package_version != entry.version {
            return Err(AgentError::Registry);
        }
        let installed = npm::install(staging.path(), &package.package).await?;
        (
            installed.executable,
            package.args.clone(),
            package.env.clone(),
            true,
            installed.version,
        )
    } else {
        return Err(AgentError::Platform);
    };
    if !staging.path().join(&executable).is_file() {
        return Err(AgentError::Install);
    }
    let directory = format!(
        "{}-{}-{}",
        entry.id,
        installed_version,
        uuid::Uuid::new_v4().simple()
    );
    tokio::fs::rename(staging.path(), root.join(&directory))
        .await
        .map_err(|_| AgentError::Storage)?;
    Ok(Installed {
        entry,
        installed_version: Some(installed_version),
        directory,
        executable,
        args,
        env,
        node,
        model: None,
        thought_level: None,
    })
}
enum BinaryFormat {
    Zip,
    TarGz,
    Raw,
}
fn binary_format(binary: &Binary) -> Result<BinaryFormat, AgentError> {
    let url = https(&binary.archive)?;
    let name = url.path().rsplit('/').next().ok_or(AgentError::Registry)?;
    if name.ends_with(".zip") {
        Ok(BinaryFormat::Zip)
    } else if name.ends_with(".tar.gz") || name.ends_with(".tgz") {
        Ok(BinaryFormat::TarGz)
    } else if name.ends_with(".exe") || name.ends_with(".par") || !name.contains('.') {
        Ok(BinaryFormat::Raw)
    } else {
        Err(AgentError::Platform)
    }
}
fn extract(archive: &Path, output: &Path, binary: &Binary) -> Result<(), AgentError> {
    std::fs::create_dir_all(output).map_err(|_| AgentError::Storage)?;
    let format = binary_format(binary)?;
    let input = std::fs::File::open(archive).map_err(|_| AgentError::Storage)?;
    let mut total = 0u64;
    if matches!(format, BinaryFormat::Zip) {
        let mut zip = zip::ZipArchive::new(input).map_err(|_| AgentError::Install)?;
        if zip.len() > 100_000 {
            return Err(AgentError::Install);
        }
        for i in 0..zip.len() {
            let mut entry = zip.by_index(i).map_err(|_| AgentError::Install)?;
            if entry.is_dir() && matches!(entry.name(), "." | "./") {
                continue;
            }
            let path = output.join(relative(entry.name())?);
            if entry.is_symlink() {
                return Err(AgentError::Install);
            }
            total = total.checked_add(entry.size()).ok_or(AgentError::Install)?;
            if total > MAX_EXPANDED {
                return Err(AgentError::Install);
            }
            if entry.is_dir() {
                std::fs::create_dir_all(path).map_err(|_| AgentError::Storage)?;
                continue;
            }
            std::fs::create_dir_all(path.parent().ok_or(AgentError::Install)?)
                .map_err(|_| AgentError::Storage)?;
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)
                .map_err(|_| AgentError::Install)?;
            let size = entry.size();
            copy_entry(&mut entry, &mut file, size)?;
            executable(&path, entry.unix_mode().unwrap_or(0o644))?;
        }
    } else if matches!(format, BinaryFormat::TarGz) {
        let mut tar = tar::Archive::new(flate2::read::GzDecoder::new(input));
        for (i, entry) in tar.entries().map_err(|_| AgentError::Install)?.enumerate() {
            if i > 100_000 {
                return Err(AgentError::Install);
            }
            let mut entry = entry.map_err(|_| AgentError::Install)?;
            let path = entry
                .path()
                .map_err(|_| AgentError::Install)?
                .to_string_lossy()
                .into_owned();
            let kind = entry.header().entry_type();
            if kind.is_dir() && matches!(path.as_str(), "." | "./") {
                continue;
            }
            let target = output.join(relative(&path)?);
            if kind.is_dir() {
                std::fs::create_dir_all(target).map_err(|_| AgentError::Storage)?;
                continue;
            }
            if !kind.is_file() {
                return Err(AgentError::Install);
            }
            total = total.checked_add(entry.size()).ok_or(AgentError::Install)?;
            if total > MAX_EXPANDED {
                return Err(AgentError::Install);
            }
            std::fs::create_dir_all(target.parent().ok_or(AgentError::Install)?)
                .map_err(|_| AgentError::Storage)?;
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&target)
                .map_err(|_| AgentError::Install)?;
            let size = entry.size();
            copy_entry(&mut entry, &mut file, size)?;
            executable(&target, entry.header().mode().unwrap_or(0o644))?;
        }
    } else {
        let target = output.join(relative(&binary.cmd)?);
        std::fs::create_dir_all(target.parent().ok_or(AgentError::Install)?)
            .map_err(|_| AgentError::Storage)?;
        std::fs::copy(archive, &target).map_err(|_| AgentError::Install)?;
    }
    executable(&output.join(relative(&binary.cmd)?), 0o755)
}
fn copy_entry(
    input: impl std::io::Read,
    output: &mut std::fs::File,
    size: u64,
) -> Result<(), AgentError> {
    let copied =
        std::io::copy(&mut input.take(size + 1), output).map_err(|_| AgentError::Install)?;
    if copied != size {
        return Err(AgentError::Install);
    }
    Ok(())
}
fn executable(path: &Path, mode: u32) -> Result<(), AgentError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode & 0o755))
            .map_err(|_| AgentError::Storage)?;
    }
    #[cfg(not(unix))]
    let _ = (path, mode);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[tokio::test]
    #[ignore = "reads public Registry metadata over HTTPS; never installs or launches agents"]
    async fn public_registry_metadata_includes_primary_agents() {
        let entries = fetch().await.unwrap();
        for id in ["codex-acp", "claude-acp", "antigravity-acp"] {
            let entry = entries.iter().find(|e| e.id == id).unwrap();
            assert!(
                entry.supported(),
                "{id} has no supported distribution for this platform"
            );
        }
    }

    fn binary(extension: &str) -> Binary {
        Binary {
            archive: format!("https://example.test/agent{extension}"),
            sha256: None,
            cmd: "bin/agent".into(),
            args: vec![],
            env: BTreeMap::new(),
        }
    }

    #[test]
    fn zip_installs_nested_executable_but_rejects_escape_and_links() {
        for name in ["bin/agent", "../escaped", "/absolute", "link"] {
            let temp = tempfile::tempdir().unwrap();
            let archive = temp.path().join("download");
            let mut zip = zip::ZipWriter::new(std::fs::File::create(&archive).unwrap());
            let options = zip::write::SimpleFileOptions::default();
            if name == "link" {
                zip.add_symlink(name, "../escaped", options).unwrap();
            } else {
                zip.start_file(name, options).unwrap();
                zip.write_all(b"synthetic executable").unwrap();
            }
            zip.finish().unwrap();
            let output = temp.path().join("output");
            let result = extract(&archive, &output, &binary(".zip"));
            assert_eq!(result.is_ok(), name == "bin/agent");
            assert!(!temp.path().join("escaped").exists());
            if result.is_ok() {
                assert_eq!(
                    std::fs::read(output.join(name)).unwrap(),
                    b"synthetic executable"
                );
            }
        }
    }

    #[test]
    fn tar_rejects_links_and_unsupported_compression() {
        let temp = tempfile::tempdir().unwrap();
        let archive = temp.path().join("download");
        let gzip = flate2::write::GzEncoder::new(
            std::fs::File::create(&archive).unwrap(),
            flate2::Compression::default(),
        );
        let mut tar = tar::Builder::new(gzip);
        let mut header = tar::Header::new_gnu();
        header.set_entry_type(tar::EntryType::Symlink);
        header.set_size(0);
        header.set_mode(0o755);
        tar.append_link(&mut header, "bin/agent", "../../escaped")
            .unwrap();
        tar.into_inner().unwrap().finish().unwrap();
        assert!(extract(&archive, &temp.path().join("output"), &binary(".tar.gz")).is_err());
        assert!(!temp.path().join("escaped").exists());
        assert!(binary_format(&binary(".tar.bz2")).is_err());
        assert!(binary_format(&binary(".tar.xz")).is_err());
    }

    #[test]
    fn tar_accepts_root_directory_and_nested_executable() {
        let temp = tempfile::tempdir().unwrap();
        let archive = temp.path().join("download");
        let gzip = flate2::write::GzEncoder::new(
            std::fs::File::create(&archive).unwrap(),
            flate2::Compression::default(),
        );
        let mut tar = tar::Builder::new(gzip);
        let mut header = tar::Header::new_gnu();
        header.set_entry_type(tar::EntryType::Directory);
        header.set_mode(0o755);
        header.set_size(0);
        header.set_cksum();
        tar.append_data(&mut header, "./", std::io::empty())
            .unwrap();
        header.set_entry_type(tar::EntryType::Regular);
        header.set_size(9);
        header.set_cksum();
        tar.append_data(&mut header, "bin/agent", &b"synthetic"[..])
            .unwrap();
        tar.into_inner().unwrap().finish().unwrap();
        let output = temp.path().join("output");
        extract(&archive, &output, &binary(".tar.gz")).unwrap();
        assert_eq!(
            std::fs::read(output.join("bin/agent")).unwrap(),
            b"synthetic"
        );
    }

    #[test]
    fn installed_manifest_survives_reload_and_rejects_paths_outside_install_root() {
        let temp = tempfile::tempdir().unwrap();
        let entry: Entry = serde_json::from_value(serde_json::json!({
            "id":"synthetic", "name":"Synthetic", "version":"1.0.0", "distribution":{}
        }))
        .unwrap();
        let mut records = BTreeMap::from([(
            "synthetic".into(),
            Installed {
                entry,
                installed_version: None,
                directory: "synthetic-1".into(),
                executable: "bin/agent".into(),
                args: vec![],
                env: BTreeMap::new(),
                node: false,
                model: None,
                thought_level: None,
            },
        )]);
        save(temp.path(), &records).unwrap();
        assert_eq!(load(temp.path()).unwrap()["synthetic"].version(), "1.0.0");
        records.get_mut("synthetic").unwrap().installed_version = Some("0.9.0".into());
        save(temp.path(), &records).unwrap();
        let reloaded = load(temp.path()).unwrap();
        assert_eq!(reloaded["synthetic"].version(), "0.9.0");
        assert_eq!(reloaded["synthetic"].entry.version, "1.0.0");
        records.get_mut("synthetic").unwrap().directory = "../outside".into();
        save(temp.path(), &records).unwrap();
        assert!(load(temp.path()).is_err());
    }
    #[test]
    fn package_pins_and_archive_paths_reject_injection_and_escape() {
        for value in [
            "agent",
            "agent@latest",
            "--help@1",
            "https://example.test/a@1",
            "@scope/../../x@1",
            "agent@1;echo",
            "agent@1",
            "agent@1.0",
            "agent@1.0.0 || *",
        ] {
            assert!(npm::package_spec(value).is_err(), "{value}");
        }
        assert_eq!(
            npm::package_spec("@agentclientprotocol/codex-acp@2.0.1").unwrap(),
            ("@agentclientprotocol/codex-acp", "2.0.1")
        );
        for value in [
            "../escape",
            "/absolute",
            "dir/../../escape",
            "C:\\escape",
            "dir\\escape",
        ] {
            assert!(relative(value).is_err());
        }
        assert!(relative("./bin/agent").is_ok());
    }
}
