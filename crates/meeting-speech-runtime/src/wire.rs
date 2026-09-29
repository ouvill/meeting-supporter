use crate::{Error, Transcript};
use serde::Deserialize;

#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub(super) enum Event {
    Ready { protocol: u8 },
    Listening {},
    Progress {},
    Segment { segment: Segment },
    Stopped {},
    Error { code: WorkerError },
}
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum WorkerError {
    MicrophoneUnavailable,
    ModelUnavailable,
    CaptureOverflow,
    CaptureFailed,
}
impl From<WorkerError> for Error {
    fn from(code: WorkerError) -> Self {
        match code {
            WorkerError::MicrophoneUnavailable => Self::MicrophoneUnavailable,
            WorkerError::ModelUnavailable => Self::ModelUnavailable,
            WorkerError::CaptureOverflow => Self::CaptureOverflow,
            WorkerError::CaptureFailed => Self::CaptureFailed,
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Segment {
    start_sample: u64,
    end_sample: u64,
    generation: u64,
    recognition: Recognition,
    samples: usize,
    voiced_frames: usize,
    segment_frames: usize,
    rms_dbfs: f64,
    accepted: bool,
}
#[derive(Deserialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
enum Recognition {
    NotRequested {},
    Rejected {},
    Recognized {
        text: String,
        punctuation: Option<Punctuation>,
    },
}
#[derive(Deserialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
enum Punctuation {
    Applied { text: String, processing_ms: f64 },
    Failed { code: String },
}

impl Segment {
    pub(super) fn transcript(self, generation: u64) -> Result<Option<Transcript>, Error> {
        if self.end_sample.checked_sub(self.start_sample) != Some(self.samples as u64)
            || self.generation != 0
            || self.voiced_frames > self.segment_frames
            || !self.rms_dbfs.is_finite()
        {
            return Err(Error::InvalidWorkerOutput);
        }
        match self.recognition {
            Recognition::Recognized { text, punctuation } if self.accepted => {
                if text.len() > 16 * 1024 {
                    return Err(Error::InvalidWorkerOutput);
                }
                let (display, failed) = match punctuation {
                    Some(Punctuation::Applied {
                        text,
                        processing_ms,
                    }) => {
                        if !processing_ms.is_finite()
                            || processing_ms < 0.0
                            || text.len() > 32 * 1024
                        {
                            return Err(Error::InvalidWorkerOutput);
                        }
                        (text, false)
                    }
                    Some(Punctuation::Failed { code }) => {
                        if code.len() > 128 {
                            return Err(Error::InvalidWorkerOutput);
                        }
                        (text.clone(), true)
                    }
                    None => (text.clone(), false),
                };
                Ok(Some(Transcript {
                    generation,
                    sequence: 0,
                    text: display,
                    raw_text: text,
                    start_sample: self.start_sample,
                    end_sample: self.end_sample,
                    punctuation_failed: failed,
                }))
            }
            Recognition::Rejected {} if !self.accepted => Ok(None),
            _ => Err(Error::InvalidWorkerOutput),
        }
    }
}
