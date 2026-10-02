//! Desktop-owned speech session state. Native inference lives in a disposable child.
mod wire;
use serde::Serialize;
use std::{
    io::{BufRead, BufReader, Read, Write},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};
use thiserror::Error;
use wire::Event;

const MAX_LINE: u64 = 64 * 1024;
const MAX_TRANSCRIPTS: usize = 2000;
const PREPARE_TIMEOUT: Duration = Duration::from_secs(120);
const PROGRESS_TIMEOUT: Duration = Duration::from_secs(30);
const STOP_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq, Error)]
#[serde(rename_all = "snake_case")]
pub enum Error {
    #[error("busy")]
    Busy,
    #[error("worker_unavailable")]
    WorkerUnavailable,
    #[error("model_unavailable")]
    ModelUnavailable,
    #[error("microphone_unavailable")]
    MicrophoneUnavailable,
    #[error("capture_failed")]
    CaptureFailed,
    #[error("capture_overflow")]
    CaptureOverflow,
    #[error("worker_failed")]
    WorkerFailed,
    #[error("invalid_worker_output")]
    InvalidWorkerOutput,
    #[error("prepare_timeout")]
    PrepareTimeout,
    #[error("inference_timeout")]
    InferenceTimeout,
    #[error("stop_timeout")]
    StopTimeout,
    #[error("transcript_limit")]
    TranscriptLimit,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Idle,
    Starting,
    Listening,
    Stopping,
    Stopped,
    Failed,
}

#[derive(Clone, Debug, Serialize)]
pub struct Transcript {
    pub generation: u64,
    pub sequence: usize,
    pub text: String,
    pub raw_text: String,
    pub start_sample: u64,
    pub end_sample: u64,
    pub punctuation_failed: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct Snapshot {
    pub generation: u64,
    pub revision: u64,
    pub phase: Phase,
    pub error: Option<Error>,
    pub transcripts: Vec<Transcript>,
}

impl Default for Snapshot {
    fn default() -> Self {
        Self {
            generation: 0,
            revision: 0,
            phase: Phase::Idle,
            error: None,
            transcripts: Vec::new(),
        }
    }
}

#[derive(Clone)]
pub struct Config {
    pub worker: PathBuf,
    pub model: PathBuf,
    pub punctuation: Option<PathBuf>,
    pub device: Option<usize>,
}

struct Active {
    stop: Arc<AtomicBool>,
    task: JoinHandle<()>,
}

/// Only one worker can own the input device. Snapshots survive UI reloads.
#[derive(Default)]
pub struct Runtime {
    snapshot: Arc<Mutex<Snapshot>>,
    active: Mutex<Option<Active>>,
}

impl Runtime {
    pub fn snapshot(&self) -> Snapshot {
        self.snapshot
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    pub fn start(&self, config: Config) -> Result<Snapshot, Error> {
        let mut active = self.active.lock().unwrap_or_else(|e| e.into_inner());
        if active.as_ref().is_some_and(|a| !a.task.is_finished()) {
            return Err(Error::Busy);
        }
        if let Some(previous) = active.take() {
            let _ = previous.task.join();
        }
        if !config.worker.is_file() {
            return Err(Error::WorkerUnavailable);
        }
        if !config.model.is_dir() || config.punctuation.as_ref().is_some_and(|p| !p.is_dir()) {
            return Err(Error::ModelUnavailable);
        }
        let generation = {
            let mut state = self.snapshot.lock().unwrap_or_else(|e| e.into_inner());
            if state.transcripts.len() >= MAX_TRANSCRIPTS {
                return Err(Error::TranscriptLimit);
            }
            state.generation = state.generation.checked_add(1).ok_or(Error::WorkerFailed)?;
            state.phase = Phase::Starting;
            state.error = None;
            state.revision += 1;
            state.generation
        };
        let stop = Arc::new(AtomicBool::new(false));
        let signal = Arc::clone(&stop);
        let shared = Arc::clone(&self.snapshot);
        let task = thread::Builder::new()
            .name("speech-supervisor".into())
            .spawn(move || {
                let result = supervise(config, &signal, &shared, generation);
                update(&shared, generation, |state| {
                    state.phase = if result.is_ok() {
                        Phase::Stopped
                    } else {
                        Phase::Failed
                    };
                    state.error = result.err();
                });
            })
            .map_err(|_| {
                update(&self.snapshot, generation, |state| {
                    state.phase = Phase::Failed;
                    state.error = Some(Error::WorkerFailed);
                });
                Error::WorkerFailed
            })?;
        *active = Some(Active { stop, task });
        Ok(self.snapshot())
    }

    pub fn stop(&self) -> Snapshot {
        let active = self.active.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(active) = active.as_ref().filter(|a| !a.task.is_finished()) {
            active.stop.store(true, Ordering::Release);
            let mut state = self.snapshot.lock().unwrap_or_else(|e| e.into_inner());
            if matches!(state.phase, Phase::Starting | Phase::Listening) {
                state.phase = Phase::Stopping;
                state.revision += 1;
            }
        }
        self.snapshot()
    }

    pub fn clear(&self) -> Result<Snapshot, Error> {
        let active = self.active.lock().unwrap_or_else(|e| e.into_inner());
        if active.as_ref().is_some_and(|a| !a.task.is_finished()) {
            return Err(Error::Busy);
        }
        let mut state = self.snapshot.lock().unwrap_or_else(|e| e.into_inner());
        state.transcripts.clear();
        state.revision += 1;
        Ok(state.clone())
    }

    pub fn shutdown(&self) {
        let mut active = self.active.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(active) = active.take() {
            active.stop.store(true, Ordering::Release);
            let _ = active.task.join();
        }
    }
}
impl Drop for Runtime {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn update(state: &Mutex<Snapshot>, generation: u64, operation: impl FnOnce(&mut Snapshot)) {
    let mut state = state.lock().unwrap_or_else(|e| e.into_inner());
    if state.generation == generation {
        operation(&mut state);
        state.revision += 1;
    }
}

/// Worker never starts subprocesses. Kill and reap even on malformed output or early returns.
struct OwnedChild(Child);
impl Drop for OwnedChild {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn command(config: &Config) -> Command {
    let mut command = Command::new(&config.worker);
    command
        .args(["--mic", "--desktop", "--reazon-model"])
        .arg(&config.model);
    if let Some(path) = &config.punctuation {
        command.arg("--punctuation-model").arg(path);
    }
    if let Some(device) = config.device {
        command.arg("--input-device").arg(device.to_string());
    }
    // Carry only OS audio/runtime settings, never cloud credentials or Python state.
    command.env_clear();
    for key in [
        "SystemRoot",
        "WINDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "XDG_RUNTIME_DIR",
        "PULSE_SERVER",
        "PULSE_COOKIE",
        "DBUS_SESSION_BUS_ADDRESS",
    ] {
        if let Some(value) = std::env::var_os(key) {
            command.env(key, value);
        }
    }
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    command
}

enum ReadEvent {
    Event(Event),
    Ended,
    Failed,
}
fn supervise(
    config: Config,
    stop: &AtomicBool,
    state: &Mutex<Snapshot>,
    generation: u64,
) -> Result<(), Error> {
    let mut child = OwnedChild(
        command(&config)
            .spawn()
            .map_err(|_| Error::WorkerUnavailable)?,
    );
    let stdout = child.0.stdout.take().ok_or(Error::WorkerFailed)?;
    let (sender, receiver) = mpsc::sync_channel(32);
    let overflow = Arc::new(AtomicBool::new(false));
    let reader_overflow = Arc::clone(&overflow);
    let reader = thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        loop {
            let mut line = Vec::new();
            let message = match (&mut reader)
                .take(MAX_LINE + 1)
                .read_until(b'\n', &mut line)
            {
                Ok(0) => ReadEvent::Ended,
                Ok(_) if line.len() as u64 <= MAX_LINE => match serde_json::from_slice(&line) {
                    Ok(event) => ReadEvent::Event(event),
                    Err(_) => ReadEvent::Failed,
                },
                _ => ReadEvent::Failed,
            };
            let done = !matches!(message, ReadEvent::Event(_));
            if sender.try_send(message).is_err() {
                reader_overflow.store(true, Ordering::Release);
                break;
            }
            if done {
                break;
            }
        }
    });
    let result = monitor(&mut child.0, receiver, &overflow, stop, state, generation);
    drop(child); // closes stdout before joining the reader
    let _ = reader.join();
    result
}

fn monitor(
    child: &mut Child,
    receiver: mpsc::Receiver<ReadEvent>,
    overflow: &AtomicBool,
    stop: &AtomicBool,
    state: &Mutex<Snapshot>,
    generation: u64,
) -> Result<(), Error> {
    let started = Instant::now();
    let mut progress = started;
    let mut stopping = None;
    let mut ready = false;
    let mut listening = false;
    let mut finished = false;
    loop {
        if stop.load(Ordering::Acquire) && stopping.is_none() {
            stopping = Some(Instant::now());
            if let Some(mut input) = child.stdin.take() {
                let _ = input.write_all(b"{\"op\":\"stop\"}\n");
            }
        }
        if stopping.is_some_and(|at: Instant| at.elapsed() > STOP_TIMEOUT) {
            return Err(Error::StopTimeout);
        }
        if stopping.is_none() {
            if !listening && started.elapsed() > PREPARE_TIMEOUT {
                return Err(Error::PrepareTimeout);
            }
            if listening && progress.elapsed() > PROGRESS_TIMEOUT {
                return Err(Error::InferenceTimeout);
            }
        }
        if overflow.load(Ordering::Acquire) {
            return Err(Error::InvalidWorkerOutput);
        }
        match receiver.recv_timeout(Duration::from_millis(50)) {
            Ok(ReadEvent::Event(Event::Ready { protocol: 1 })) if !ready && !finished => {
                ready = true
            }
            Ok(ReadEvent::Event(Event::Listening {})) if ready && !listening && !finished => {
                listening = true;
                progress = Instant::now();
                update(state, generation, |s| {
                    if s.phase == Phase::Starting {
                        s.phase = Phase::Listening;
                    }
                });
            }
            Ok(ReadEvent::Event(Event::Progress {})) if listening && !finished => {
                progress = Instant::now()
            }
            Ok(ReadEvent::Event(Event::Segment { segment })) if listening && !finished => {
                let transcript = segment.transcript(generation)?;
                if let Some(mut transcript) = transcript {
                    let mut state = state.lock().unwrap_or_else(|e| e.into_inner());
                    if state.generation != generation {
                        return Err(Error::WorkerFailed);
                    }
                    if state.transcripts.len() >= MAX_TRANSCRIPTS {
                        return Err(Error::TranscriptLimit);
                    }
                    transcript.sequence = state.transcripts.len();
                    state.transcripts.push(transcript);
                    state.revision += 1;
                }
                progress = Instant::now();
            }
            Ok(ReadEvent::Event(Event::Stopped {})) if ready && !finished && stopping.is_some() => {
                finished = true
            }
            Ok(ReadEvent::Event(Event::Error { code })) => return Err(code.into()),
            Ok(ReadEvent::Ended) | Err(mpsc::RecvTimeoutError::Disconnected) => {
                // A stopped message is insufficient: the child must also exit successfully.
                if let Some(status) = child.try_wait().map_err(|_| Error::WorkerFailed)? {
                    return if finished && status.success() {
                        Ok(())
                    } else {
                        Err(Error::WorkerFailed)
                    };
                }
                if finished {
                    thread::sleep(Duration::from_millis(10));
                } else {
                    return Err(Error::WorkerFailed);
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            _ => return Err(Error::InvalidWorkerOutput),
        }
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::{fs, os::unix::fs::PermissionsExt, sync::atomic::AtomicU64};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    struct Fixture(PathBuf);
    impl Fixture {
        fn new(script: &str) -> Self {
            let directory = std::env::temp_dir().join(format!(
                "meeting-worker-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&directory).unwrap();
            let path = directory.join("worker");
            fs::write(&path, format!("#!/bin/sh\n{script}\n")).unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
            Self(directory)
        }
        fn config(&self) -> Config {
            Config {
                worker: self.0.join("worker"),
                model: self.0.clone(),
                punctuation: None,
                device: None,
            }
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn wait(runtime: &Runtime, target: Phase) -> Snapshot {
        let start = Instant::now();
        loop {
            let state = runtime.snapshot();
            if state.phase == target {
                return state;
            }
            assert!(start.elapsed() < Duration::from_secs(8), "{:?}", state);
            thread::sleep(Duration::from_millis(10));
        }
    }

    #[test]
    fn stop_flushes_and_reaps_before_restart_and_preserves_snapshots() {
        let fixture = Fixture::new(
            r#"
printf '%s\n' '{"type":"ready","protocol":1}' '{"type":"listening"}'
IFS= read -r command
[ "$command" = '{"op":"stop"}' ] || exit 9
printf '%s\n' '{"type":"segment","segment":{"start_sample":0,"end_sample":480,"generation":0,"samples":480,"voiced_frames":1,"segment_frames":1,"rms_dbfs":-12,"accepted":true,"recognition":{"status":"recognized","text":"確認です","punctuation":{"status":"applied","text":"確認です。","processing_ms":1}}}}' '{"type":"stopped"}'
"#,
        );
        let runtime = Runtime::default();
        runtime.start(fixture.config()).unwrap();
        wait(&runtime, Phase::Listening);
        assert_eq!(runtime.start(fixture.config()).unwrap_err(), Error::Busy);
        assert_eq!(runtime.clear().unwrap_err(), Error::Busy);
        runtime.stop();
        let ended = wait(&runtime, Phase::Stopped);
        assert_eq!(ended.transcripts[0].text, "確認です。");
        assert_eq!(ended.transcripts[0].raw_text, "確認です");
        runtime.shutdown();
        runtime.start(fixture.config()).unwrap();
        wait(&runtime, Phase::Listening);
        runtime.stop();
        let ended = wait(&runtime, Phase::Stopped);
        assert_eq!(ended.transcripts.len(), 2);
        assert_eq!(ended.transcripts[0].generation, 1);
        assert_eq!(ended.transcripts[1].generation, 2);
        runtime.shutdown();
        assert!(runtime.clear().unwrap().transcripts.is_empty());
    }

    #[test]
    fn unexpected_exit_is_not_a_successful_stop() {
        let fixture = Fixture::new("printf '%s\\n' '{\"type\":\"ready\",\"protocol\":1}'; exit 0");
        let runtime = Runtime::default();
        runtime.start(fixture.config()).unwrap();
        assert_eq!(
            wait(&runtime, Phase::Failed).error,
            Some(Error::WorkerFailed)
        );
    }

    #[test]
    fn malformed_output_retires_the_worker() {
        let fixture = Fixture::new(
            "printf '%s\\n' '{\"type\":\"ready\",\"protocol\":99}'; exec /bin/sleep 20",
        );
        let runtime = Runtime::default();
        runtime.start(fixture.config()).unwrap();
        assert_eq!(
            wait(&runtime, Phase::Failed).error,
            Some(Error::InvalidWorkerOutput)
        );
    }

    #[test]
    fn stuck_worker_is_killed_after_stop_deadline() {
        let fixture = Fixture::new("printf '%s\\n' '{\"type\":\"ready\",\"protocol\":1}' '{\"type\":\"listening\"}'; exec /bin/sleep 20");
        let runtime = Runtime::default();
        runtime.start(fixture.config()).unwrap();
        wait(&runtime, Phase::Listening);
        runtime.stop();
        assert_eq!(
            wait(&runtime, Phase::Failed).error,
            Some(Error::StopTimeout)
        );
        runtime.shutdown();
    }

    #[test]
    fn model_error_is_public_and_does_not_leave_a_running_worker() {
        let fixture = Fixture::new("printf '%s\\n' '{\"type\":\"ready\",\"protocol\":1}' '{\"type\":\"error\",\"code\":\"model_unavailable\"}'; exit 1");
        let runtime = Runtime::default();
        runtime.start(fixture.config()).unwrap();
        assert_eq!(
            wait(&runtime, Phase::Failed).error,
            Some(Error::ModelUnavailable)
        );
    }
}
