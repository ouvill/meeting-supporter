mod capture;
mod convert;
#[cfg(target_os = "linux")]
mod pulse;
mod recording;

use recording::Recorder;
use serde::{Deserialize, Serialize};
use std::{
    io::{self, BufRead, Read, Write},
    path::PathBuf,
    sync::mpsc::{self, SyncSender, TryRecvError, TrySendError},
    thread,
    time::{Duration, Instant},
};
use thiserror::Error;

#[derive(Debug, Error, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Error {
    #[error("audio_device_unavailable")]
    Device,
    #[error("audio_capture_failed")]
    Capture,
    #[error("audio_recording_failed")]
    Recording,
    #[error("audio_recording_busy")]
    RecordingBusy,
    #[error("audio_protocol_failed")]
    Protocol,
    #[error("audio_transport_failed")]
    Transport,
    #[error("audio_platform_unsupported")]
    Unsupported,
}

#[derive(Serialize)]
pub struct Device {
    index: String,
    name: String,
    is_monitor: bool,
    is_default: bool,
    hostapi: &'static str,
    capture: &'static str,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    id: u64,
    command: Command,
}
#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Command {
    StartRecording { path: PathBuf },
    StopRecording {},
    Shutdown {},
}

struct Packet {
    metadata: serde_json::Value,
    pcm: Option<Box<[u8; 960]>>,
}
fn send(sender: &SyncSender<Packet>, metadata: serde_json::Value) -> Result<(), Error> {
    sender
        .try_send(Packet {
            metadata,
            pcm: None,
        })
        .map_err(|_| Error::Transport)
}
fn write_packet(writer: &mut impl Write, packet: Packet) -> io::Result<()> {
    let header = serde_json::to_vec(&packet.metadata)?;
    writer.write_all(&(header.len() as u32).to_le_bytes())?;
    writer.write_all(&(if packet.pcm.is_some() { 960u32 } else { 0u32 }).to_le_bytes())?;
    writer.write_all(&header)?;
    if let Some(pcm) = packet.pcm {
        writer.write_all(pcm.as_ref())?;
    }
    writer.flush()
}

fn controls(sender: SyncSender<Request>) {
    let mut input = io::stdin().lock();
    loop {
        let mut line = Vec::new();
        if input
            .by_ref()
            .take(16_385)
            .read_until(b'\n', &mut line)
            .is_err()
            || line.len() > 16_384
            || !line.ends_with(b"\n")
        {
            break;
        }
        let Ok(request) = serde_json::from_slice(&line) else {
            break;
        };
        if sender.try_send(request).is_err() {
            break;
        }
    }
    // EOF/malformed control drops the channel; main finalizes and exits.
}

fn run_capture(device: Device, output: &SyncSender<Packet>) -> Result<(), Error> {
    let (capture_tx, capture_rx) = mpsc::sync_channel(200);
    let capture_failed = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let failure = std::sync::Arc::clone(&capture_failed);
    let _capture = capture::start(device.index, capture_tx, failure);
    match capture_rx.recv_timeout(Duration::from_secs(5)) {
        Ok(capture::CaptureEvent::Started) => {}
        _ => return Err(Error::Device),
    }
    send(
        output,
        serde_json::json!({"type":"ready", "protocol":1, "name":device.name, "rate":16000}),
    )?;
    let (control_tx, control_rx) = mpsc::sync_channel(16);
    thread::spawn(move || controls(control_tx));
    let mut recorder = Recorder::default();
    let mut sequence = 0u64;
    let mut progress = Instant::now();
    loop {
        if capture_failed.load(std::sync::atomic::Ordering::Acquire)
            || progress.elapsed() >= Duration::from_secs(5)
        {
            let _ = recorder.stop();
            return Err(Error::Capture);
        }
        match control_rx.try_recv() {
            Ok(request) => {
                let result = match request.command {
                    Command::StartRecording { path } => recorder.start(path).map(|_| serde_json::json!({"type":"recording_started", "id":request.id})),
                    Command::StopRecording {} => recorder.stop().map(|recording| serde_json::json!({"type":"recording_stopped", "id":request.id, "recording":recording})),
                    Command::Shutdown {} => {
                        recorder.stop()?;
                        send(output, serde_json::json!({"type":"stopped", "id":request.id}))?;
                        return Ok(());
                    }
                };
                let metadata = result.unwrap_or_else(
                    |code| serde_json::json!({"type":"error", "id":request.id, "code":code}),
                );
                send(output, metadata)?;
            }
            Err(TryRecvError::Disconnected) => {
                recorder.stop()?;
                return Ok(());
            }
            Err(TryRecvError::Empty) => {}
        }
        match capture_rx.recv_timeout(Duration::from_millis(5)) {
            Ok(capture::CaptureEvent::Frame(pcm)) => {
                progress = Instant::now();
                if recorder.write(&pcm).is_err() {
                    send(
                        output,
                        serde_json::json!({"type":"recording_error", "code":"recording"}),
                    )?;
                }
                let peak = pcm
                    .as_chunks::<2>()
                    .0
                    .iter()
                    .map(|p| f64::from(i16::from_le_bytes([p[0], p[1]])).abs() / 32768.0)
                    .fold(0.0, f64::max);
                let packet = Packet {
                    metadata: serde_json::json!({"type":"audio", "sequence":sequence, "peak":peak}),
                    pcm: Some(pcm),
                };
                match output.try_send(packet) {
                    Ok(()) | Err(TrySendError::Full(_)) => {} // Sequence exposes STT transport gaps; recording continues.
                    Err(TrySendError::Disconnected(_)) => return Err(Error::Transport),
                }
                sequence = sequence.checked_add(1).ok_or(Error::Capture)?;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            _ => {
                let _ = recorder.stop();
                return Err(Error::Capture);
            }
        }
    }
}

fn run() -> Result<(), Error> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let devices = capture::devices()?;
    if arguments == ["--list-devices"] {
        write_packet(
            &mut io::stdout().lock(),
            Packet {
                metadata: serde_json::json!({"type":"devices", "devices":devices}),
                pcm: None,
            },
        )
        .map_err(|_| Error::Transport)?;
        return Ok(());
    }
    let mut role = None;
    let mut selected = None;
    let mut args = arguments.iter();
    while let Some(option) = args.next() {
        match option.as_str() {
            "--role" if role.is_none() => role = args.next().map(String::as_str),
            "--device" if selected.is_none() => selected = args.next().map(String::as_str),
            _ => return Err(Error::Protocol),
        }
    }
    let monitor = match role {
        Some("other") => true,
        Some("self") => false,
        _ => return Err(Error::Protocol),
    };
    let device = devices
        .into_iter()
        .find(|d| match selected {
            Some(id) => d.index == id,
            None => d.is_default && d.is_monitor == monitor,
        })
        .ok_or(Error::Device)?;
    let (sender, receiver) = mpsc::sync_channel(64);
    let writer = thread::spawn(move || {
        let mut output = io::stdout().lock();
        for packet in receiver {
            write_packet(&mut output, packet)?;
        }
        Ok::<_, io::Error>(())
    });
    let result = run_capture(device, &sender);
    if let Err(code) = &result {
        let _ = send(
            &sender,
            serde_json::json!({"type":"error", "id":null, "code":code}),
        );
    }
    drop(sender);
    writer
        .join()
        .map_err(|_| Error::Transport)?
        .map_err(|_| Error::Transport)?;
    result
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn controls_validate_the_real_request_envelope() {
        let request: Request = serde_json::from_str(
            r#"{"id":1,"command":{"op":"start_recording","path":"synthetic.wav"}}"#,
        )
        .unwrap();
        assert!(matches!(request.command, Command::StartRecording { .. }));
        assert!(serde_json::from_str::<Request>(
            r#"{"id":1,"command":{"op":"shutdown","unknown":true}}"#
        )
        .is_err());
        assert!(serde_json::from_str::<Request>(r#"{"id":1,"op":"shutdown"}"#).is_err());
    }

    #[test]
    fn packet_keeps_binary_pcm_outside_json() {
        let mut bytes = Vec::new();
        write_packet(
            &mut bytes,
            Packet {
                metadata: serde_json::json!({"type":"audio","sequence":7}),
                pcm: Some(Box::new([123; 960])),
            },
        )
        .unwrap();
        let header = u32::from_le_bytes(bytes[..4].try_into().unwrap()) as usize;
        assert_eq!(u32::from_le_bytes(bytes[4..8].try_into().unwrap()), 960);
        assert_eq!(&bytes[8 + header..], &[123; 960]);
    }
}
