//! Bounded, on-demand invocation of the shared Python executable. No shell or runtime install.
use serde::{Deserialize, Serialize};
use std::{
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};
use thiserror::Error;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    process::Command,
    sync::watch,
};

const MAX_RESPONSE: u64 = 3 * 1024 * 1024;
const MAX_REQUEST: usize = 64 * 1024;
const TIMEOUT: Duration = Duration::from_secs(60);
#[derive(Debug, Error)]
pub(crate) enum WorkerError {
    #[error("資料変換workerを起動できません。配置を確認してください。")]
    Unavailable,
    #[error("資料変換workerとの通信に失敗しました。")]
    Transport,
    #[error("資料変換workerの応答形式が不正です。")]
    Protocol,
    #[error("資料変換が制限時間を超えました。")]
    Timeout,
    #[error("資料変換を中止しました。")]
    Cancelled,
}
impl WorkerError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::Unavailable => "worker_unavailable",
            Self::Transport => "worker_failed",
            Self::Protocol => "worker_protocol_error",
            Self::Timeout => "conversion_timeout",
            Self::Cancelled => "conversion_cancelled",
        }
    }
}
#[derive(Serialize)]
pub(crate) struct Input {
    pub id: usize,
    pub input_path: PathBuf,
}
#[derive(Serialize)]
struct Request<'a> {
    protocol: u32,
    id: &'a str,
    files: &'a [Input],
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Response {
    protocol: u32,
    id: String,
    results: Vec<DocumentResult>,
}
#[derive(Deserialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum DocumentResult {
    Converted { id: usize, markdown: String },
    Failed { id: usize, error: ConversionError },
}
impl DocumentResult {
    pub fn id(&self) -> usize {
        match self {
            Self::Converted { id, .. } | Self::Failed { id, .. } => *id,
        }
    }
}
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ConversionError {
    ConversionFailed,
}
fn decode(bytes: &[u8], id: &str, files: &[Input]) -> Result<Vec<DocumentResult>, WorkerError> {
    let response: Response = serde_json::from_slice(bytes).map_err(|_| WorkerError::Protocol)?;
    if response.protocol != 1 || response.id != id || response.results.len() != files.len() {
        return Err(WorkerError::Protocol);
    }
    let mut seen = std::collections::HashSet::new();
    for result in &response.results {
        if !files.iter().any(|f| f.id == result.id()) || !seen.insert(result.id()) {
            return Err(WorkerError::Protocol);
        }
        if let DocumentResult::Converted { markdown, .. } = result {
            if markdown.chars().count() > 40_000 {
                return Err(WorkerError::Protocol);
            }
        }
    }
    Ok(response.results)
}
pub(crate) async fn convert(
    executable: &Path,
    directory: &Path,
    files: &[Input],
    stopping: watch::Receiver<bool>,
) -> Result<Vec<DocumentResult>, WorkerError> {
    run(executable, directory, files, stopping, TIMEOUT).await
}
async fn run(
    executable: &Path,
    directory: &Path,
    files: &[Input],
    mut stopping: watch::Receiver<bool>,
    deadline: Duration,
) -> Result<Vec<DocumentResult>, WorkerError> {
    if *stopping.borrow() {
        return Err(WorkerError::Cancelled);
    }
    let id = uuid::Uuid::new_v4().to_string();
    let bytes = serde_json::to_vec(&Request {
        protocol: 1,
        id: &id,
        files,
    })
    .map_err(|_| WorkerError::Protocol)?;
    if bytes.len() > MAX_REQUEST {
        return Err(WorkerError::Protocol);
    }
    let mut command = Command::new(executable);
    command
        .arg("convert-document")
        .current_dir(directory)
        .env_clear()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    // Keep only OS loader requirements; AI keys and user Python settings are not inherited.
    for key in ["SYSTEMROOT", "WINDIR", "PATH"] {
        if let Some(value) = std::env::var_os(key) {
            command.env(key, value);
        }
    }
    command
        .env("TMPDIR", directory)
        .env("TMP", directory)
        .env("TEMP", directory)
        .env("PYTHONUTF8", "1")
        .env("PYTHONNOUSERSITE", "1");
    let mut child = command.spawn().map_err(|_| WorkerError::Unavailable)?;
    let result = tokio::select! {
        value = tokio::time::timeout(deadline, async {
            let mut stdin = child.stdin.take().ok_or(WorkerError::Transport)?;
            let stdout = child.stdout.take().ok_or(WorkerError::Transport)?;
            stdin.write_all(&bytes).await.map_err(|_| WorkerError::Transport)?;
            stdin.shutdown().await.map_err(|_| WorkerError::Transport)?;
            drop(stdin);
            let mut output = Vec::new();
            stdout.take(MAX_RESPONSE + 1).read_to_end(&mut output).await
                .map_err(|_| WorkerError::Transport)?;
            if output.len() as u64 > MAX_RESPONSE { return Err(WorkerError::Protocol); }
            let status = child.wait().await.map_err(|_| WorkerError::Transport)?;
            if !status.success() { return Err(WorkerError::Transport); }
            decode(&output, &id, files)
        }) => value.unwrap_or(Err(WorkerError::Timeout)),
        _ = stopping.wait_for(|stop| *stop) => Err(WorkerError::Cancelled),
    };
    // Reap before the caller drops the temporary directory, including timeout/cancellation.
    if result.is_err() {
        let _ = child.start_kill();
        let _ = child.wait().await;
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn rejects_mismatched_or_duplicated_results_and_unbounded_text() {
        let files = vec![Input {
            id: 0,
            input_path: "/synthetic/0.docx".into(),
        }];
        let good = json!({"protocol":1,"id":"request","results":[{"id":0,"status":"converted","markdown":"synthetic"}]});
        assert!(decode(&serde_json::to_vec(&good).unwrap(), "request", &files).is_ok());
        for bad in [
            json!({"protocol":2,"id":"request","results":good["results"]}),
            json!({"protocol":1,"id":"wrong","results":good["results"]}),
            json!({"protocol":1,"id":"request","results":[]}),
            json!({"protocol":1,"id":"request","results":[{"id":1,"status":"converted","markdown":"synthetic"}]}),
            json!({"protocol":1,"id":"request","results":[{"id":0,"status":"converted","markdown":"a".repeat(40_001)}]}),
        ] {
            assert!(decode(&serde_json::to_vec(&bad).unwrap(), "request", &files).is_err());
        }
        let duplicated = json!({"protocol":1,"id":"request","results":[good["results"][0].clone(),good["results"][0].clone()]});
        let files = vec![
            Input {
                id: 0,
                input_path: "0.docx".into(),
            },
            Input {
                id: 1,
                input_path: "1.docx".into(),
            },
        ];
        assert!(decode(&serde_json::to_vec(&duplicated).unwrap(), "request", &files).is_err());
    }
    #[cfg(target_os = "linux")]
    #[tokio::test]
    async fn timeout_and_cancellation_reap_worker_before_returning() {
        use std::os::unix::fs::PermissionsExt;
        for cancelled in [false, true] {
            let temp = tempfile::tempdir().unwrap();
            let script = temp.path().join("worker");
            std::fs::write(&script, "#!/bin/sh\necho $$ > pid\nwhile :; do :; done\n").unwrap();
            std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o700)).unwrap();
            let (tx, rx) = watch::channel(false);
            let signal = async {
                if cancelled {
                    while !temp.path().join("pid").exists() {
                        tokio::time::sleep(Duration::from_millis(5)).await;
                    }
                    tx.send(true).unwrap();
                }
            };
            let (result, ()) = tokio::join!(
                run(&script, temp.path(), &[], rx, Duration::from_millis(250)),
                signal
            );
            assert!(matches!(
                (cancelled, result),
                (true, Err(WorkerError::Cancelled)) | (false, Err(WorkerError::Timeout))
            ));
            let pid = std::fs::read_to_string(temp.path().join("pid")).unwrap();
            assert!(!Path::new(&format!("/proc/{}", pid.trim())).exists());
        }
    }
    #[cfg(target_os = "linux")]
    #[tokio::test]
    async fn oversized_output_is_bounded_and_worker_is_reaped() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let script = temp.path().join("worker");
        std::fs::write(
            &script,
            "#!/bin/sh\necho $$ > pid\nwhile :; do printf '%01024d' 0; done\n",
        )
        .unwrap();
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o700)).unwrap();
        let (tx, rx) = watch::channel(false);
        assert!(matches!(
            run(&script, temp.path(), &[], rx, Duration::from_secs(5)).await,
            Err(WorkerError::Protocol)
        ));
        let pid = std::fs::read_to_string(temp.path().join("pid")).unwrap();
        assert!(!Path::new(&format!("/proc/{}", pid.trim())).exists());
        drop(tx);
    }
}
