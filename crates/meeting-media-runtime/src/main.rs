use meeting_media_runtime::{wire::*, Options, Supervisor};
use serde::Deserialize;
use std::path::PathBuf;
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt},
    sync::{mpsc, watch},
};
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    id: u64,
    command: Command,
}

fn options() -> Result<Options, Error> {
    let mut args = std::env::args().skip(1);
    let mut audio = None;
    let mut speech = None;
    let mut role = None;
    let mut device = None;
    while let Some(option) = args.next() {
        let value = args.next().ok_or(Error::Protocol)?;
        match option.as_str() {
            "--audio-worker" if audio.is_none() => audio = Some(PathBuf::from(value)),
            "--speech-worker" if speech.is_none() => speech = Some(PathBuf::from(value)),
            "--device" if device.is_none() => device = Some(value),
            "--role" if role.is_none() => {
                role = Some(match value.as_str() {
                    "self" => Role::User,
                    "other" => Role::Other,
                    _ => return Err(Error::Protocol),
                })
            }
            _ => return Err(Error::Protocol),
        }
    }
    Ok(Options {
        audio_worker: audio.ok_or(Error::Protocol)?,
        speech_worker: speech.ok_or(Error::Protocol)?,
        role: role.ok_or(Error::Protocol)?,
        device,
    })
}
async fn run() -> Result<(), Error> {
    let options = options()?;
    let (events, mut output) = mpsc::channel(128);
    let (closed, mut closing) = watch::channel(false);
    let output_closed = closed.clone();
    let writer = tokio::spawn(async move {
        let mut stdout = tokio::io::stdout();
        while let Some(event) = output.recv().await {
            let result = async {
                let mut bytes = serde_json::to_vec(&event)?;
                if bytes.len() >= 65536 {
                    return Err(Error::Protocol);
                }
                bytes.push(b'\n');
                stdout.write_all(&bytes).await?;
                stdout.flush().await?;
                Ok::<_, Error>(())
            }
            .await;
            if result.is_err() {
                let _ = output_closed.send(true);
                return;
            }
        }
    });
    let (commands, mut input) = mpsc::channel(16);
    let (interrupt, mut interrupted) = watch::channel(0u64);
    tokio::spawn(async move {
        let mut stdin = tokio::io::BufReader::new(tokio::io::stdin());
        loop {
            let mut bytes = Vec::new();
            let read = (&mut stdin).take(16385).read_until(b'\n', &mut bytes).await;
            if read.is_err() || bytes.len() > 16384 || !bytes.ends_with(b"\n") {
                break;
            }
            let Ok(request) = serde_json::from_slice::<Request>(&bytes) else {
                break;
            };
            if matches!(
                request.command,
                Command::ShutdownSpeech {} | Command::Shutdown {}
            ) {
                interrupt.send_modify(|version| *version += 1);
            }
            if commands.try_send(request).is_err() {
                break;
            }
        }
        let _ = closed.send(true);
    });
    let mut supervisor = tokio::select! {
        result = Supervisor::open(options, events.clone()) => result?,
        _ = closing.changed() => return Ok(()),
    };
    loop {
        let request = tokio::select! {
            biased;
            _ = closing.changed() => break,
            request = input.recv() => match request { Some(request) => request, None => break },
        };
        let shutdown = matches!(request.command, Command::Shutdown {});
        let preparing = matches!(request.command, Command::Prepare { .. });
        if matches!(
            request.command,
            Command::ShutdownSpeech {} | Command::Shutdown {}
        ) {
            interrupted.borrow_and_update();
        }
        let result = tokio::select! {
            biased;
            _ = closing.changed() => break,
            _ = interrupted.changed(), if preparing => Err(Error::Speech),
            result = supervisor.execute(request.command) => result,
        };
        let event = match result {
            Ok(recording) => Event::Result {
                id: request.id,
                generation: supervisor.generation(),
                recording,
            },
            Err(code) => Event::Error {
                id: request.id,
                code,
            },
        };
        if !matches!(
            tokio::time::timeout(std::time::Duration::from_secs(5), events.send(event)).await,
            Ok(Ok(()))
        ) || shutdown
        {
            break;
        }
    }
    let _ = supervisor.close().await;
    drop(supervisor);
    drop(events);
    // A non-reading client must not hold the children alive or hang shutdown.
    let mut writer = writer;
    if tokio::time::timeout(std::time::Duration::from_secs(5), &mut writer)
        .await
        .is_err()
    {
        writer.abort();
    }
    Ok(())
}
#[tokio::main(flavor = "current_thread")]
async fn main() {
    if run().await.is_err() {
        eprintln!("media runtime failed");
        std::process::exit(1);
    }
}
