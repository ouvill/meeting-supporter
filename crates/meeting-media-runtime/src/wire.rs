use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use thiserror::Error;

#[derive(Debug, Clone, Copy, Error, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Error {
    #[error("invalid_protocol")]
    Protocol,
    #[error("worker_unavailable")]
    Worker,
    #[error("worker_timeout")]
    Timeout,
    #[error("capture_failed")]
    Capture,
    #[error("input_discontinuity")]
    Discontinuity,
    #[error("gpu_unavailable")]
    GpuUnavailable,
    #[error("speech_failed")]
    Speech,
    #[error("recording_failed")]
    Recording,
    #[error("worker_shutdown_failed")]
    Shutdown,
    #[error("media_busy")]
    Busy,
}
impl From<std::io::Error> for Error {
    fn from(_: std::io::Error) -> Self {
        Self::Worker
    }
}
impl From<serde_json::Error> for Error {
    fn from(_: serde_json::Error) -> Self {
        Self::Protocol
    }
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    #[serde(rename = "self")]
    User,
    Other,
}
impl Role {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::User => "self",
            Self::Other => "other",
        }
    }
}
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SpeechConfig {
    pub vad_threshold: f32,
    pub silence_seconds: f64,
    pub min_voiced_ms: usize,
    pub min_voiced_ratio: f64,
    pub min_rms_dbfs: f64,
}
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(tag = "engine", rename_all = "snake_case", deny_unknown_fields)]
pub enum Recognizer {
    #[default]
    Reazonspeech,
    Whisper {
        device: InferenceDevice,
        language: Language,
    },
}
#[derive(Debug, Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum InferenceDevice {
    Auto,
    Cpu,
    Gpu,
}
#[derive(Debug, Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Language {
    Ja,
    En,
    Auto,
}
#[derive(Debug, Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionDevice {
    Cpu,
    Gpu,
}
impl ExecutionDevice {
    pub fn argument(self) -> &'static str {
        match self {
            Self::Cpu => "cpu",
            Self::Gpu => "gpu",
        }
    }
}
impl InferenceDevice {
    pub fn argument(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Cpu => "cpu",
            Self::Gpu => "gpu",
        }
    }
}
impl Language {
    pub fn argument(self) -> &'static str {
        match self {
            Self::Ja => "ja",
            Self::En => "en",
            Self::Auto => "auto",
        }
    }
}
#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    Prepare {
        #[serde(default)]
        recognizer: Recognizer,
        model: PathBuf,
        punctuation: Option<PathBuf>,
        config: SpeechConfig,
    },
    StartSpeech {},
    StopSpeech {},
    ShutdownSpeech {},
    StartRecording {
        path: PathBuf,
    },
    StopRecording {},
    Shutdown {},
}
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Recording {
    pub size_bytes: u64,
    pub started_ms: u64,
    pub ended_ms: u64,
    pub samples: u64,
}
#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum CaptureEvent {
    Ready {
        protocol: u8,
        rate: u32,
        name: String,
    },
    Audio {
        sequence: u64,
        peak: f64,
    },
    RecordingStarted {
        id: u64,
    },
    RecordingStopped {
        id: u64,
        recording: Option<Recording>,
    },
    RecordingError {},
    Stopped {
        id: u64,
    },
    Error {
        id: Option<u64>,
    },
}
#[derive(Debug, Deserialize)]
pub struct SpeechReply {
    pub id: Option<u64>,
    #[serde(flatten)]
    pub body: SpeechBody,
}
#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SpeechBody {
    Ready {
        protocol: u8,
        transcription_available: bool,
    },
    Prepared {
        #[serde(default)]
        execution_device: Option<ExecutionDevice>,
    },
    Configured {},
    Reset {},
    Audio {
        segment: Option<Segment>,
    },
    Finished {
        segment: Option<Segment>,
    },
    Error {
        code: SpeechFailure,
    },
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SpeechFailure {
    GpuUnavailable,
    #[serde(other)]
    Other,
}
#[derive(Debug, Deserialize)]
pub struct Segment {
    pub generation: u64,
    pub start_sample: u64,
    pub end_sample: u64,
    pub recognition: Recognition,
}
#[derive(Debug, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum Recognition {
    NotRequested,
    Rejected,
    Recognized {
        text: String,
        punctuation: Option<Punctuation>,
    },
}
#[derive(Debug, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum Punctuation {
    Applied { text: String },
    Failed {},
}

#[derive(Debug, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Event {
    #[serde(skip)]
    Drained {
        done: tokio::sync::oneshot::Sender<()>,
    },
    Ready {
        protocol: u8,
        name: String,
        rate: u32,
    },
    ExecutionDevice {
        device: ExecutionDevice,
    },
    SpeechLag {},
    Level {
        peak: f64,
    },
    Transcript {
        generation: u64,
        text: String,
        raw_text: String,
        start_sample: u64,
        end_sample: u64,
        punctuation_failed: bool,
    },
    SpeechError {
        code: Error,
    },
    CaptureError {},
    RecordingError {},
    Result {
        id: u64,
        generation: u64,
        recording: Option<Recording>,
    },
    Error {
        id: u64,
        code: Error,
    },
}
