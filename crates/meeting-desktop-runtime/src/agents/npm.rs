//! npm installation with bounded, structured diagnostics. Never expose npm output.
use super::{registry::identifier, registry::relative, AgentError};
use semver::{Version, VersionReq};
use serde::Deserialize;
use std::{path::Path, process::Stdio, time::Duration};
use tokio::{io::AsyncReadExt, process::Command};

const MAX_OUTPUT: u64 = 64 * 1024;

pub(super) struct Package {
    pub executable: String,
    pub version: String,
}

pub(super) fn package_spec(value: &str) -> Result<(&str, &str), AgentError> {
    let (name, version) = value.rsplit_once('@').ok_or(AgentError::Registry)?;
    let valid_name = if let Some(scoped) = name.strip_prefix('@') {
        scoped
            .split_once('/')
            .is_some_and(|(scope, package)| identifier(scope) && identifier(package))
    } else {
        identifier(name) && name.as_bytes()[0].is_ascii_alphanumeric()
    };
    if !valid_name || version.len() > 100 || Version::parse(version).is_err() {
        return Err(AgentError::Registry);
    }
    Ok((name, version))
}

pub(super) async fn install(directory: &Path, package: &str) -> Result<Package, AgentError> {
    install_with(directory, package, || {
        Command::new(if cfg!(windows) { "npm.cmd" } else { "npm" })
    })
    .await
}

async fn install_with(
    directory: &Path,
    package: &str,
    command: impl Fn() -> Command,
) -> Result<Package, AgentError> {
    execute(directory, package, &command, false).await?;
    let (name, ceiling) = package_spec(package)?;
    read_package(directory, name, ceiling).await
}

pub(super) async fn available(directory: &Path, package: &str) -> Result<String, AgentError> {
    available_with(directory, package, || {
        Command::new(if cfg!(windows) { "npm.cmd" } else { "npm" })
    })
    .await
}

async fn available_with(
    directory: &Path,
    package: &str,
    command: impl Fn() -> Command,
) -> Result<String, AgentError> {
    execute(directory, package, command, true).await?;
    #[derive(Deserialize)]
    struct Lockfile {
        packages: std::collections::BTreeMap<String, Locked>,
    }
    #[derive(Deserialize)]
    struct Locked {
        version: Option<String>,
    }
    let path = directory.join("package-lock.json");
    if tokio::fs::metadata(&path)
        .await
        .map_err(|_| AgentError::Install)?
        .len()
        > 8 * 1024 * 1024
    {
        return Err(AgentError::Install);
    }
    let lock: Lockfile = serde_json::from_slice(
        &tokio::fs::read(path)
            .await
            .map_err(|_| AgentError::Install)?,
    )
    .map_err(|_| AgentError::Install)?;
    let (name, ceiling) = package_spec(package)?;
    let version = lock
        .packages
        .get(&format!("node_modules/{name}"))
        .and_then(|entry| entry.version.as_ref())
        .ok_or(AgentError::Install)?;
    validate_version(version, ceiling)?;
    Ok(version.clone())
}

async fn execute(
    directory: &Path,
    package: &str,
    command: impl Fn() -> Command,
    lock_only: bool,
) -> Result<(), AgentError> {
    let (name, ceiling) = package_spec(package)?;
    // Windows の npm.cmd でも < がリダイレクトにならない範囲表記を使う。
    let bounded = format!("{name}@0.0.0 - {ceiling}");
    let mut install = command();
    install
        .current_dir(directory)
        .args([
            "install",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--omit=dev",
            "--include=optional",
            "--global=false",
            "--package-lock=true",
            "--dry-run=false",
            "--save-exact",
            "--json",
            "--loglevel=error",
            "--logs-max=0",
            "--prefix",
        ])
        .arg(directory)
        .arg(if lock_only {
            "--package-lock-only"
        } else {
            "--package-lock-only=false"
        })
        .arg("--")
        .arg(bounded);
    let (success, output) = run(&mut install, Duration::from_secs(300)).await?;
    if success {
        return Ok(());
    }
    let code = error_code(&output);
    if code.as_deref() == Some("ETARGET") {
        // 導入可能な版がない場合も、利用者の公開日制限を解除しない。
        // 関連する設定だけ読み取り、外部出力をそのまま表示しない。
        let mut query = command();
        query
            .current_dir(directory)
            .args([
                "config",
                "get",
                "before",
                "min-release-age",
                "--logs-max=0",
                "--prefix",
            ])
            .arg(directory);
        if let Ok((true, settings)) = run(&mut query, Duration::from_secs(5)).await {
            if let Some(error) = release_policy(&settings) {
                return Err(error);
            }
        }
    }
    Err(classify(code.as_deref()))
}

fn validate_version(value: &str, ceiling: &str) -> Result<(), AgentError> {
    let version = Version::parse(value).map_err(|_| AgentError::Integrity)?;
    let requirement =
        VersionReq::parse(&format!(">=0.0.0, <={ceiling}")).map_err(|_| AgentError::Registry)?;
    if value.len() > 100 || !requirement.matches(&version) {
        return Err(AgentError::Integrity);
    }
    Ok(())
}

async fn read_package(directory: &Path, name: &str, ceiling: &str) -> Result<Package, AgentError> {
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum Bin {
        Path(String),
        Named(std::collections::BTreeMap<String, String>),
    }
    #[derive(Deserialize)]
    struct Manifest {
        name: String,
        version: String,
        bin: Bin,
    }
    let bytes = tokio::fs::read(
        directory
            .join("node_modules")
            .join(name)
            .join("package.json"),
    )
    .await
    .map_err(|_| AgentError::Install)?;
    let manifest: Manifest = serde_json::from_slice(&bytes).map_err(|_| AgentError::Install)?;
    validate_version(&manifest.version, ceiling)?;
    if manifest.name != name {
        return Err(AgentError::Integrity);
    }
    let bin = match &manifest.bin {
        Bin::Path(path) => Some(path),
        Bin::Named(bins) if bins.len() == 1 => bins.values().next(),
        Bin::Named(bins) => bins.get(name.rsplit('/').next().unwrap_or(name)),
    }
    .ok_or(AgentError::Install)?;
    let executable = format!("node_modules/{name}/{}", relative(bin)?.to_string_lossy());
    if !directory.join(&executable).is_file() {
        return Err(AgentError::Install);
    }
    Ok(Package {
        executable,
        version: manifest.version,
    })
}

async fn run(command: &mut Command, deadline: Duration) -> Result<(bool, Vec<u8>), AgentError> {
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    let mut child = command.spawn().map_err(|_| AgentError::Node)?;
    let result = tokio::time::timeout(deadline, async {
        let stdout = child.stdout.take().ok_or(AgentError::Install)?;
        let mut output = Vec::new();
        stdout
            .take(MAX_OUTPUT + 1)
            .read_to_end(&mut output)
            .await
            .map_err(|_| AgentError::Install)?;
        if output.len() as u64 > MAX_OUTPUT {
            return Err(AgentError::Install);
        }
        let status = child.wait().await.map_err(|_| AgentError::Install)?;
        Ok((status.success(), output))
    })
    .await
    .unwrap_or(Err(AgentError::InstallTimeout));
    if result.is_err() {
        let _ = child.kill().await;
    }
    result
}

fn error_code(output: &[u8]) -> Option<String> {
    #[derive(Deserialize)]
    struct Report {
        error: Failure,
    }
    #[derive(Deserialize)]
    struct Failure {
        code: String,
    }
    let report: Report = serde_json::from_slice(output).ok()?;
    Some(report.error.code)
}

fn release_policy(output: &[u8]) -> Option<AgentError> {
    let settings = std::str::from_utf8(output).ok()?;
    let before = settings
        .lines()
        .find_map(|line| line.strip_prefix("before="));
    if before.is_some_and(|value| !matches!(value.trim(), "" | "null" | "undefined")) {
        return Some(AgentError::NpmBefore);
    }
    let days = settings
        .lines()
        .find_map(|line| line.strip_prefix("min-release-age="))?
        .trim()
        .parse::<f64>()
        .ok()?;
    if days.is_finite() && days > 0.0 {
        Some(AgentError::NpmReleaseAge { days })
    } else {
        None
    }
}

fn classify(code: Option<&str>) -> AgentError {
    match code {
        Some("ETARGET" | "E404") => AgentError::NpmVersion,
        Some("EACCES" | "EPERM" | "EROFS") => AgentError::NpmPermission,
        Some("ENOSPC") => AgentError::NpmSpace,
        Some("EBADENGINE") => AgentError::NpmEngine,
        Some(
            "ENOTFOUND"
            | "EAI_AGAIN"
            | "ETIMEDOUT"
            | "ECONNRESET"
            | "ECONNREFUSED"
            | "E503"
            | "E502"
            | "UNABLE_TO_VERIFY_LEAF_SIGNATURE"
            | "CERT_HAS_EXPIRED"
            | "SELF_SIGNED_CERT_IN_CHAIN",
        ) => AgentError::NpmNetwork,
        Some("E401" | "E403" | "ENEEDAUTH") => AgentError::NpmRegistryAuth,
        Some("EINTEGRITY") => AgentError::Integrity,
        _ => AgentError::Install,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn diagnostics_never_surface_external_messages_or_unknown_codes() {
        for (code, expected) in [
            ("ETARGET", "バージョン"),
            ("EACCES", "書き込み権限"),
            ("ENOTFOUND", "通信"),
            ("EBADENGINE", "Node.js"),
            ("ENOSPC", "空き容量"),
            ("E401", "認証"),
            ("synthetic-secret", "導入できません"),
        ] {
            let output = serde_json::json!({"error":{"code":code,"summary":"synthetic-secret","detail":"synthetic-secret"}}).to_string();
            let error = classify(error_code(output.as_bytes()).as_deref()).to_string();
            assert!(error.contains(expected));
            assert!(!error.contains("synthetic-secret"));
        }
        assert!(error_code(b"synthetic-secret").is_none());
        assert!(error_code(br#"{"error":{"code":123}}"#).is_none());
    }

    #[test]
    fn release_restrictions_are_distinguished_without_echoing_configuration() {
        assert!(
            matches!(release_policy(b"before=null\nmin-release-age=1\n"), Some(AgentError::NpmReleaseAge { days }) if days == 1.0)
        );
        assert!(matches!(
            release_policy(b"before=2026-01-01T00:00:00.000Z\nmin-release-age=1\n"),
            Some(AgentError::NpmBefore)
        ));
        for settings in [
            "before=null\nmin-release-age=0",
            "min-release-age=NaN",
            "min-release-age=inf",
            "min-release-age=synthetic-secret",
        ] {
            assert!(release_policy(settings.as_bytes()).is_none());
        }
    }

    #[tokio::test]
    async fn installed_manifest_must_match_package_and_version_ceiling() {
        let temp = tempfile::tempdir().unwrap();
        let package = temp.path().join("node_modules/synthetic-agent");
        std::fs::create_dir_all(&package).unwrap();
        std::fs::write(package.join("index.js"), "").unwrap();
        for (name, version, ceiling, valid) in [
            ("synthetic-agent", "0.9.0", "1.0.1", true),
            ("synthetic-agent", "1.0.1", "1.0.1", true),
            ("synthetic-agent", "1.0.1+build", "1.0.1", true),
            ("synthetic-agent", "1.0.2", "1.0.1", false),
            ("synthetic-agent", "1.0.1-beta.1", "1.0.1", false),
            ("synthetic-agent", "1.0.1-beta.1", "1.0.1-beta.2", true),
            ("synthetic-agent", "1.0.1", "1.0.1-beta.2", false),
            ("synthetic-agent", "latest", "1.0.1", false),
            ("other-agent", "1.0.0", "1.0.1", false),
        ] {
            std::fs::write(
                package.join("package.json"),
                serde_json::json!({
                    "name":name, "version":version, "bin":{"synthetic-agent":"index.js"}
                })
                .to_string(),
            )
            .unwrap();
            let result = read_package(temp.path(), "synthetic-agent", ceiling).await;
            assert_eq!(result.is_ok(), valid, "{name}@{version} / {ceiling}");
            if let Ok(installed) = result {
                assert_eq!(installed.version, version);
                assert_eq!(
                    installed.executable,
                    "node_modules/synthetic-agent/index.js"
                );
            }
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    #[ignore = "requires Node.js, npm 12+, and a synthetic loopback registry"]
    async fn npm_release_age_with_synthetic_registry() {
        use axum::{routing::get, Json, Router};
        use chrono::{TimeDelta, Utc};
        use flate2::{write::GzEncoder, Compression};

        let temp = tempfile::tempdir().unwrap();
        let root = temp.path();
        let directory = root.join("install");
        std::fs::create_dir(&directory).unwrap();
        let manifest = serde_json::json!({
            "name":"synthetic-agent", "version":"1.0.0", "bin":"index.js",
            "scripts":{"postinstall":"node -e \"require('fs').writeFileSync('script-ran', 'yes')\""}
        });
        let mut tar = tar::Builder::new(GzEncoder::new(Vec::new(), Compression::default()));
        for (path, bytes) in [
            ("package/package.json", manifest.to_string().into_bytes()),
            ("package/index.js", b"#!/usr/bin/env node\n".to_vec()),
        ] {
            let mut header = tar::Header::new_gnu();
            header.set_size(bytes.len() as u64);
            header.set_mode(0o755);
            header.set_cksum();
            tar.append_data(&mut header, path, bytes.as_slice())
                .unwrap();
        }
        let archive = tar.into_inner().unwrap().finish().unwrap();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let metadata = serde_json::json!({
            "name":"synthetic-agent", "dist-tags":{"latest":"1.0.2"},
            "versions":{
                "1.0.0":{
                    "name":"synthetic-agent", "version":"1.0.0",
                    "dist":{"tarball":format!("http://{address}/archive.tgz")}
                },
                "1.0.1":{
                    "name":"synthetic-agent", "version":"1.0.1",
                    "dist":{"tarball":format!("http://{address}/never-download.tgz")}
                },
                "1.0.2":{
                    "name":"synthetic-agent", "version":"1.0.2",
                    "dist":{"tarball":format!("http://{address}/above-ceiling.tgz")}
                }
            },
            "time":{
                "1.0.0":(Utc::now() - TimeDelta::days(3)).to_rfc3339(),
                "1.0.1":Utc::now().to_rfc3339(),
                "1.0.2":(Utc::now() - TimeDelta::days(2)).to_rfc3339()
            }
        });
        let app = Router::new()
            .route(
                "/synthetic-agent",
                get(move || {
                    let metadata = metadata.clone();
                    async move { Json(metadata) }
                }),
            )
            .route(
                "/archive.tgz",
                get(move || {
                    let archive = archive.clone();
                    async move { archive }
                }),
            );
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let config = format!("registry=http://{address}/\nmin-release-age=1\n");
        std::fs::write(root.join("user.npmrc"), &config).unwrap();
        std::fs::write(root.join("global.npmrc"), "").unwrap();
        let command = || {
            let mut command = Command::new("npm");
            command
                .env_clear()
                .env("PATH", std::env::var_os("PATH").unwrap_or_default())
                .env("HOME", root)
                .env("NPM_CONFIG_USERCONFIG", root.join("user.npmrc"))
                .env("NPM_CONFIG_GLOBALCONFIG", root.join("global.npmrc"))
                .env("NPM_CONFIG_CACHE", root.join("cache"));
            command
        };
        let mut version = command();
        version.arg("--version");
        let (success, output) = run(&mut version, Duration::from_secs(5)).await.unwrap();
        assert!(success);
        let major = std::str::from_utf8(&output)
            .unwrap()
            .trim()
            .split('.')
            .next()
            .unwrap()
            .parse::<u32>()
            .unwrap();
        assert!(major >= 12, "this test requires npm 12+");

        let probe = root.join("probe");
        std::fs::create_dir(&probe).unwrap();
        assert_eq!(
            available_with(&probe, "synthetic-agent@1.0.1", &command)
                .await
                .unwrap(),
            "1.0.0"
        );
        assert!(!probe.join("node_modules").exists());

        let result = install_with(&directory, "synthetic-agent@0.0.0", &command).await;
        assert!(matches!(result, Err(AgentError::NpmReleaseAge { days }) if days == 1.0));
        assert!(!directory.join("node_modules/synthetic-agent").exists());
        let installed = install_with(&directory, "synthetic-agent@1.0.1", &command)
            .await
            .unwrap();
        assert_eq!(installed.version, "1.0.0");
        assert_eq!(
            installed.executable,
            "node_modules/synthetic-agent/index.js"
        );
        let repeated = install_with(&directory, "synthetic-agent@1.0.1", &command)
            .await
            .unwrap();
        assert_eq!(repeated.version, "1.0.0");
        let package = directory.join("node_modules/synthetic-agent");
        assert!(package.join("index.js").is_file());
        assert!(!package.join("script-ran").exists());
        assert_eq!(
            std::fs::read_to_string(root.join("user.npmrc")).unwrap(),
            config
        );
        server.abort();
    }
}
