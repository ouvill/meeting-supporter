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
    SileroWhisper {
        model: PathBuf,
        device: crate::whisper::Device,
        language: crate::whisper::Language,
    },
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
    recognizer: Option<Recognizer>,
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
            SpeechPlan::SileroWhisper {
                model,
                device,
                language,
            } => (
                Some(Recognizer::Whisper(crate::whisper::Whisper::load(
                    &model, device, language,
                )?)),
                None,
            ),
            SpeechPlan::SileroReazon {
                model_directory,
                punctuation_directory,
            } => (
                Some(Recognizer::Reazon(Reazon::load(&model_directory)?)),
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

enum Recognizer {
    Reazon(Reazon),
    Whisper(crate::whisper::Whisper),
}
impl Recognizer {
    fn transcribe(&mut self, audio: &[f32]) -> Result<String, SpeechError> {
        match self {
            Self::Reazon(model) => model.transcribe(audio),
            Self::Whisper(model) => model.transcribe(audio),
        }
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
    pub fn execution_device(&self) -> Option<&'static str> {
        match &self.state.recognizer {
            Some(Recognizer::Whisper(model)) => Some(model.device()),
            _ => None,
        }
    }

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

#[cfg(all(test, feature = "whisper"))]
mod tests {
    use super::*;

    #[test]
    #[ignore = "requires predownloaded Whisper model and ORT"]
    fn whisper_preparation_keeps_both_sources_empty() {
        let model = std::env::var_os("MEETING_TEST_WHISPER_MODEL").expect("model");
        let runtime = std::env::var_os("MEETING_TEST_ORT_LIBRARY").expect("ORT");
        let mut session = SpeechSession::new(SessionConfig {
            runtime_library: runtime.into(),
            plan: SpeechPlan::SileroWhisper {
                model: model.into(),
                device: crate::whisper::Device::Cpu,
                language: crate::whisper::Language::Ja,
            },
        })
        .prepare()
        .unwrap();
        // Warmup must not create an utterance, consume samples or prevent the
        // caller from configuring the freshly prepared session.
        assert!(matches!(
            session.execute(Command::Configure {
                vad_threshold: 0.5,
                silence_seconds: 0.4,
                min_voiced_ms: 240,
                min_voiced_ratio: 0.35,
                min_rms_dbfs: -45.0,
            }),
            Ok(Reply::Configured)
        ));
        for role in [Role::User, Role::Other] {
            assert_eq!(session.state.sources[role.index()].samples, 0);
            assert_eq!(session.state.sources[role.index()].generation, 0);
            assert!(matches!(
                session.finish(role).unwrap(),
                Reply::Finished { segment: None }
            ));
            session.execute(Command::Reset { role }).unwrap();
            assert_eq!(session.state.sources[role.index()].generation, 1);
            for _ in 0..40 {
                assert!(matches!(
                    session.audio(role, &[0; FRAME_SAMPLES]).unwrap(),
                    Reply::Audio {
                        speech: false,
                        segment: None,
                        ..
                    }
                ));
            }
            assert!(matches!(
                session.finish(role).unwrap(),
                Reply::Finished { segment: None }
            ));
        }
    }

    #[test]
    #[ignore = "requires predownloaded Whisper model, ORT and synthetic WAV"]
    fn whisper_recognizes_synthetic_audio_and_reuses_prepared_state() {
        let model = std::env::var_os("MEETING_TEST_WHISPER_MODEL").expect("model");
        let runtime = std::env::var_os("MEETING_TEST_ORT_LIBRARY").expect("ORT");
        let wav = std::env::var_os("MEETING_TEST_WHISPER_WAV").expect("synthetic WAV");
        let mut reader = hound::WavReader::open(wav).unwrap();
        assert_eq!(reader.spec().sample_rate, 16000);
        assert_eq!(reader.spec().channels, 1);
        let samples: Vec<i16> = reader.samples().collect::<Result<_, _>>().unwrap();
        let mut session = SpeechSession::new(SessionConfig {
            runtime_library: runtime.into(),
            plan: SpeechPlan::SileroWhisper {
                model: model.into(),
                device: crate::whisper::Device::Cpu,
                language: crate::whisper::Language::Ja,
            },
        })
        .prepare()
        .unwrap();
        assert_eq!(session.execution_device(), Some("cpu"));
        for generation in 1..=2 {
            session
                .execute(Command::Reset { role: Role::User })
                .unwrap();
            let mut segments = Vec::new();
            for chunk in samples.chunks(FRAME_SAMPLES) {
                let mut frame = [0i16; FRAME_SAMPLES];
                frame[..chunk.len()].copy_from_slice(chunk);
                if let Reply::Audio {
                    segment: Some(segment),
                    ..
                } = session.audio(Role::User, &frame).unwrap()
                {
                    segments.push(segment);
                }
            }
            if let Reply::Finished {
                segment: Some(segment),
            } = session.finish(Role::User).unwrap()
            {
                segments.push(segment);
            }
            assert!(segments.iter().any(|s| matches!(&s.recognition, Recognition::Recognized {text, punctuation: None} if !text.trim().is_empty())));
            assert!(
                segments
                    .iter()
                    .all(|s| s.generation == generation && s.end_sample > s.start_sample)
            );
            assert!(matches!(
                session.finish(Role::User).unwrap(),
                Reply::Finished { segment: None }
            ));
            assert!(matches!(
                session.audio(Role::User, &[0; FRAME_SAMPLES]),
                Err(SpeechError::SourceFinished)
            ));
        }
    }
}
