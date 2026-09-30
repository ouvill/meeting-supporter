//! Bounded child I/O. Child death cannot leave inference running indefinitely.
use crate::wire::Error;
use serde::de::DeserializeOwned;
use std::{path::Path, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt},
    process::{Child, ChildStdin, Command},
    time::timeout,
};

pub fn spawn(path: &Path, args: &[String], capture: bool) -> Result<Child, Error> {
    if !path.is_absolute() {
        return Err(Error::Worker);
    }
    let mut command = Command::new(path);
    command
        .args(args)
        .env_clear()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    for key in ["SYSTEMROOT", "WINDIR", "TEMP", "TMP"] {
        if let Some(value) = std::env::var_os(key) {
            command.env(key, value);
        }
    }
    if capture {
        for key in [
            "HOME",
            "XDG_RUNTIME_DIR",
            "PULSE_SERVER",
            "PULSE_COOKIE",
            "TMPDIR",
        ] {
            if let Some(value) = std::env::var_os(key) {
                command.env(key, value);
            }
        }
    }
    #[cfg(target_os = "linux")]
    {
        let parent = std::process::id() as libc::pid_t;
        // SAFETY: only async-signal-safe kernel calls run between fork and exec.
        // Check the parent after prctl to cover death before installing the signal.
        unsafe {
            command.pre_exec(move || {
                if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                if libc::getppid() != parent {
                    return Err(std::io::Error::other("parent exited"));
                }
                Ok(())
            });
        }
    }
    Ok(command.spawn()?)
}
pub async fn line<T: DeserializeOwned>(
    reader: &mut (impl AsyncBufRead + Unpin),
    limit: usize,
) -> Result<T, Error> {
    let mut bytes = Vec::new();
    let count = reader
        .take((limit + 1) as u64)
        .read_until(b'\n', &mut bytes)
        .await?;
    if count > limit || !bytes.ends_with(b"\n") {
        return Err(Error::Protocol);
    }
    Ok(serde_json::from_slice(&bytes)?)
}
pub async fn write(input: &mut ChildStdin, value: &impl serde::Serialize) -> Result<(), Error> {
    let mut data = serde_json::to_vec(value)?;
    if data.len() >= 16384 {
        return Err(Error::Protocol);
    }
    data.push(b'\n');
    timeout(Duration::from_secs(5), input.write_all(&data))
        .await
        .map_err(|_| Error::Timeout)??;
    Ok(())
}
pub async fn reap(child: &mut Child) -> Result<(), Error> {
    if child.try_wait().map_err(|_| Error::Shutdown)?.is_some() {
        return Ok(());
    }
    child.start_kill().map_err(|_| Error::Shutdown)?;
    timeout(Duration::from_secs(5), child.wait())
        .await
        .map_err(|_| Error::Shutdown)?
        .map_err(|_| Error::Shutdown)?;
    Ok(())
}
