//! Query the configured worker's build features without preparing inference.
use crate::{process, wire::Error};
use serde::Deserialize;
use std::{path::Path, time::Duration};
use tokio::{io::BufReader, time::timeout};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Capabilities {
    protocol: u8,
    whisper_gpu: bool,
}

pub async fn whisper_gpu_supported(path: &Path) -> Result<bool, Error> {
    let mut child = process::spawn(path, &["--capabilities".into()], false)?;
    drop(child.stdin.take());
    let result = timeout(Duration::from_secs(3), async {
        let mut output = BufReader::new(child.stdout.take().ok_or(Error::Worker)?);
        let capabilities: Capabilities = process::line(&mut output, 4096).await?;
        if capabilities.protocol != 1 || !child.wait().await?.success() {
            return Err(Error::Protocol);
        }
        Ok(capabilities.whisper_gpu)
    })
    .await
    .map_err(|_| Error::Timeout)
    .and_then(|result| result);
    if result.is_err() {
        process::reap(&mut child).await;
    }
    result
}
