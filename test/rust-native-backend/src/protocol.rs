use crate::error::SpeechError;
use meeting_audio_core::Segment;
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    #[serde(alias = "self")]
    User,
    Other,
}

impl Role {
    pub fn index(self) -> usize {
        match self {
            Self::User => 0,
            Self::Other => 1,
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub id: u64,
    pub command: Command,
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    Health {},
    Prepare {},
    Configure {
        vad_threshold: f32,
        silence_seconds: f64,
        min_voiced_ms: usize,
        min_voiced_ratio: f64,
        min_rms_dbfs: f64,
    },
    Audio {
        role: Role,
        pcm: Vec<i16>,
    },
    Finish {
        role: Role,
    },
    Reset {
        role: Role,
    },
    Shutdown {},
}

#[derive(Clone, Copy, Serialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum ModelStatus {
    #[default]
    Unloaded,
    Loading,
    Ready,
    Failed,
}

#[derive(Serialize)]
pub struct SegmentInfo {
    pub start_sample: u64,
    pub end_sample: u64,
    pub generation: u64,
    pub recognition: Recognition,
    samples: usize,
    voiced_frames: usize,
    segment_frames: usize,
    rms_dbfs: f64,
    accepted: bool,
}

impl SegmentInfo {
    pub(crate) fn new(
        segment: &Segment,
        end_sample: u64,
        generation: u64,
        recognition: Recognition,
    ) -> Result<Self, SpeechError> {
        Ok(Self {
            start_sample: end_sample
                .checked_sub(segment.audio.len() as u64)
                .ok_or(SpeechError::InvalidSegmentation)?,
            end_sample,
            generation,
            recognition,
            samples: segment.audio.len(),
            voiced_frames: segment.voiced_frames,
            segment_frames: segment.segment_frames,
            rms_dbfs: segment.rms_dbfs,
            accepted: segment.accepted,
        })
    }
}

#[derive(Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum Recognition {
    NotRequested,
    Rejected,
    Recognized {
        text: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        punctuation: Option<Punctuation>,
    },
}

#[derive(Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Reply {
    Segment {
        role: Role,
        segment: SegmentInfo,
    },
    Lag {
        role: Role,
    },
    Ready {
        protocol: u8,
        models: ModelStatus,
        transcription_available: bool,
    },
    Health {
        models: ModelStatus,
    },
    Prepared {
        load_ms: f64,
        #[serde(skip_serializing_if = "Option::is_none")]
        execution_device: Option<&'static str>,
    },
    Audio {
        speech: bool,
        probabilities: Vec<f32>,
        segment: Option<SegmentInfo>,
    },
    Finished {
        segment: Option<SegmentInfo>,
    },
    Configured,
    Reset,
    Stopped,
    Error {
        code: SpeechError,
    },
}

#[derive(Serialize)]
pub struct Response {
    pub id: Option<u64>,
    #[serde(flatten)]
    pub reply: Reply,
}

/// Optional presentation text; recognition text always remains the original.
#[derive(Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum Punctuation {
    Applied { text: String, processing_ms: f64 },
    Failed { code: SpeechError },
}
