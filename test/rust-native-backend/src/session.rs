use crate::{
    error::SpeechError,
    protocol::{Command, Recognition, Reply, Role, SegmentInfo},
    punctuation::Punctuator,
    reazon::Reazon,
    vad::{Vad, VadState},
};
use meeting_audio_core::{Config, FRAME_SAMPLES, Segment, Segmenter};
use std::path::PathBuf;

/// Validated compositions, not a requirement that every provider expose VAD/ASR separately.
#[derive(Clone)]
pub enum SpeechPlan {
    SileroOnly,
    SileroReazon {
        model_directory: PathBuf,
        punctuation_directory: Option<PathBuf>,
    },
}

#[derive(Clone)]
pub struct SessionConfig {
    pub runtime_library: PathBuf,
    pub plan: SpeechPlan,
}

pub struct Unprepared(SessionConfig);
pub struct Prepared {
    vad: Vad,
    segmentation: Config,
    vad_threshold: f32,
    recognizer: Option<Reazon>,
    punctuator: Option<Punctuator>,
    sources: [Source; 2],
}

/// Only a successfully prepared session has audio/finish methods.
///
/// ```compile_fail
/// use meeting_native_backend::{protocol::Role, session::{SessionConfig, SpeechPlan, SpeechSession}};
/// let mut session = SpeechSession::new(SessionConfig {
///     runtime_library: "runtime.so".into(),
///     plan: SpeechPlan::SileroOnly,
/// });
/// session.audio(Role::User, &[0; 480]);
/// ```
pub struct SpeechSession<State> {
    state: State,
}

impl SpeechSession<Unprepared> {
    pub fn new(config: SessionConfig) -> Self {
        Self {
            state: Unprepared(config),
        }
    }

    pub fn prepare(self) -> Result<SpeechSession<Prepared>, SpeechError> {
        let config = self.state.0;
        let vad = Vad::load(&config.runtime_library)?;
        let (recognizer, punctuator) = match config.plan {
            SpeechPlan::SileroOnly => (None, None),
            SpeechPlan::SileroReazon {
                model_directory,
                punctuation_directory,
            } => (
                Some(Reazon::load(&model_directory)?),
                punctuation_directory
                    .as_deref()
                    .map(Punctuator::load)
                    .transpose()?,
            ),
        };
        Ok(SpeechSession {
            state: Prepared {
                vad,
                segmentation: Config::default(),
                vad_threshold: 0.5,
                recognizer,
                punctuator,
                sources: [
                    Source::new(0, Config::default())?,
                    Source::new(0, Config::default())?,
                ],
            },
        })
    }
}

enum SourcePhase {
    Open,
    Finished,
}

struct Source {
    vad: VadState,
    segmenter: Segmenter,
    phase: SourcePhase,
    samples: u64,
    generation: u64,
}

impl Source {
    fn new(generation: u64, config: Config) -> Result<Self, SpeechError> {
        Ok(Self {
            vad: VadState::new(),
            segmenter: Segmenter::new(config).map_err(|_| SpeechError::InvalidSegmentation)?,
            phase: SourcePhase::Open,
            samples: 0,
            generation,
        })
    }
}

impl SpeechSession<Prepared> {
    pub fn execute(&mut self, command: Command) -> Result<Reply, SpeechError> {
        match command {
            Command::Configure {
                vad_threshold,
                silence_seconds,
                min_voiced_ms,
                min_voiced_ratio,
                min_rms_dbfs,
            } => {
                if !vad_threshold.is_finite()
                    || !(0.0..=1.0).contains(&vad_threshold)
                    || self.state.sources.iter().any(|s| s.samples != 0)
                {
                    return Err(SpeechError::InvalidAudioCommand);
                }
                let config = Config {
                    silence_seconds,
                    min_voiced_ms,
                    min_voiced_ratio,
                    min_rms_dbfs,
                };
                let sources = [Source::new(0, config)?, Source::new(0, config)?];
                self.state.sources = sources;
                self.state.segmentation = config;
                self.state.vad_threshold = vad_threshold;
                Ok(Reply::Configured)
            }
            Command::Audio { role, pcm } => self.audio(role, &pcm),
            Command::Finish { role } => self.finish(role),
            Command::Reset { role } => {
                let source = &mut self.state.sources[role.index()];
                *source = Source::new(
                    source
                        .generation
                        .checked_add(1)
                        .ok_or(SpeechError::InvalidSegmentation)?,
                    self.state.segmentation,
                )?;
                Ok(Reply::Reset)
            }
            _ => Err(SpeechError::InvalidAudioCommand),
        }
    }

    pub fn audio(&mut self, role: Role, pcm: &[i16]) -> Result<Reply, SpeechError> {
        if pcm.len() != FRAME_SAMPLES {
            return Err(SpeechError::InvalidFrameLength);
        }
        let source = &mut self.state.sources[role.index()];
        if matches!(source.phase, SourcePhase::Finished) {
            return Err(SpeechError::SourceFinished);
        }
        let (speech, probabilities) = self.state.vad.process_with_threshold(
            &mut source.vad,
            pcm,
            self.state.vad_threshold,
        )?;
        let bytes: Vec<u8> = pcm.iter().flat_map(|sample| sample.to_le_bytes()).collect();
        let segment = source
            .segmenter
            .push(&bytes, speech)
            .map_err(|_| SpeechError::InvalidSegmentation)?;
        source.samples = source
            .samples
            .checked_add(FRAME_SAMPLES as u64)
            .ok_or(SpeechError::InvalidSegmentation)?;
        let segment = self.recognize(role, segment)?;
        Ok(Reply::Audio {
            speech,
            probabilities,
            segment,
        })
    }

    pub fn finish(&mut self, role: Role) -> Result<Reply, SpeechError> {
        let source = &mut self.state.sources[role.index()];
        // Idempotent flush; reset explicitly begins a new source generation.
        source.phase = SourcePhase::Finished;
        let segment = source.segmenter.finish();
        Ok(Reply::Finished {
            segment: self.recognize(role, segment)?,
        })
    }

    fn recognize(
        &mut self,
        role: Role,
        segment: Option<Segment>,
    ) -> Result<Option<SegmentInfo>, SpeechError> {
        let Some(segment) = segment else {
            return Ok(None);
        };
        let source = &self.state.sources[role.index()];
        let recognition = match (&mut self.state.recognizer, segment.accepted) {
            (_, false) => Recognition::Rejected,
            (None, true) => Recognition::NotRequested,
            (Some(recognizer), true) => {
                let text = recognizer.transcribe(&segment.audio)?;
                let punctuation = self
                    .state
                    .punctuator
                    .as_mut()
                    .map(|model| model.process(&text));
                Recognition::Recognized { text, punctuation }
            }
        };
        Ok(Some(SegmentInfo::new(
            &segment,
            source.samples,
            source.generation,
            recognition,
        )?))
    }
}
