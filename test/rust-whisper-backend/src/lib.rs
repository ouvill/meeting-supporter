//! Bounded WAV probe for whisper.cpp, independent of the application's selected STT.
use serde::Serialize;
use std::{
    path::{Path, PathBuf},
    time::Instant,
};
use thiserror::Error;
use whisper_rs::{FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters};

const SAMPLE_RATE: usize = 16_000;
const MAX_SAMPLES: usize = 30 * SAMPLE_RATE;

#[derive(Debug, Error)]
pub enum Error {
    #[error("expected a nonempty mono 16 kHz PCM16 WAV, at most 30 seconds")]
    AudioFormat,
    #[error("cannot read WAV: {0}")]
    Wav(#[from] hound::Error),
    #[error("model path must be valid UTF-8")]
    ModelPath,
    #[error("GPU requested, but build has no cuda, vulkan or metal feature")]
    GpuUnavailable,
    #[error("threads must be between 1 and 64")]
    Threads,
    #[error("whisper.cpp failed: {0}")]
    Whisper(#[from] whisper_rs::WhisperError),
}

#[derive(Clone, Copy, Debug, clap::ValueEnum, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Device {
    Cpu,
    Gpu,
}
#[derive(Clone, Copy, Debug, clap::ValueEnum)]
pub enum Language {
    Ja,
    En,
    Auto,
}
impl Language {
    fn code(self) -> Option<&'static str> {
        match self {
            Self::Ja => Some("ja"),
            Self::En => Some("en"),
            Self::Auto => None,
        }
    }
}

pub struct Audio {
    samples: Vec<f32>,
}
impl Audio {
    pub fn read(path: &Path) -> Result<Self, Error> {
        let mut reader = hound::WavReader::open(path)?;
        let spec = reader.spec();
        if spec.channels != 1
            || spec.sample_rate != SAMPLE_RATE as u32
            || spec.bits_per_sample != 16
            || spec.sample_format != hound::SampleFormat::Int
            || reader.len() == 0
            || reader.len() as usize > MAX_SAMPLES
        {
            return Err(Error::AudioFormat);
        }
        let samples = reader
            .samples::<i16>()
            .map(|s| s.map(|v| f32::from(v) / 32768.0))
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self { samples })
    }
    pub fn seconds(&self) -> f64 {
        self.samples.len() as f64 / SAMPLE_RATE as f64
    }
}

pub struct Unloaded {
    model: PathBuf,
    device: Device,
}
pub struct Loaded {
    context: WhisperContext,
}
pub struct Transcriber<S> {
    state: S,
}

#[derive(Serialize)]
pub struct Preparation {
    pub load_ms: f64,
    pub model_type: String,
    /// whisper.cpp model header value; Q8_0 is 7, FP16 is 1 (quantization version removed).
    pub weight_type: i32,
    /// Requested device; native backend may fall back to CPU. This is not device detection.
    pub requested_device: Device,
}
impl Transcriber<Unloaded> {
    pub fn new(model: PathBuf, device: Device) -> Self {
        Self {
            state: Unloaded { model, device },
        }
    }
    pub fn prepare(self) -> Result<(Transcriber<Loaded>, Preparation), Error> {
        if matches!(self.state.device, Device::Gpu)
            && !cfg!(any(feature = "cuda", feature = "vulkan", feature = "metal"))
        {
            return Err(Error::GpuUnavailable);
        }
        let mut params = WhisperContextParameters::default();
        params.use_gpu(matches!(self.state.device, Device::Gpu));
        let started = Instant::now();
        let context = WhisperContext::new_with_params(
            self.state.model.to_str().ok_or(Error::ModelPath)?,
            params,
        )?;
        let info = Preparation {
            load_ms: started.elapsed().as_secs_f64() * 1000.0,
            model_type: context.model_type_readable_str()?.into(),
            weight_type: context.model_ftype() % 1000,
            requested_device: self.state.device,
        };
        Ok((
            Transcriber {
                state: Loaded { context },
            },
            info,
        ))
    }
}
#[derive(Serialize)]
pub struct Segment {
    pub start_ms: i64,
    pub end_ms: i64,
    pub text: String,
}
#[derive(Serialize)]
pub struct Transcript {
    pub audio_seconds: f64,
    pub inference_ms: f64,
    pub real_time_factor: f64,
    pub segments: Vec<Segment>,
}
impl Transcriber<Loaded> {
    pub fn transcribe(
        &self,
        audio: &Audio,
        language: Language,
        threads: i32,
    ) -> Result<Transcript, Error> {
        if !(1..=64).contains(&threads) {
            return Err(Error::Threads);
        }
        let started = Instant::now();
        let mut state = self.state.context.create_state()?;
        let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
        params.set_n_threads(threads);
        params.set_language(language.code());
        params.set_translate(false);
        params.set_no_context(true);
        params.set_temperature(0.0);
        params.set_temperature_inc(0.0);
        params.set_print_special(false);
        params.set_print_progress(false);
        params.set_print_realtime(false);
        params.set_print_timestamps(false);
        state.full(params, &audio.samples)?;
        let segments = state
            .as_iter()
            .map(|s| Segment {
                start_ms: s.start_timestamp() * 10,
                end_ms: s.end_timestamp() * 10,
                text: s.to_string(),
            })
            .collect();
        let elapsed = started.elapsed().as_secs_f64();
        Ok(Transcript {
            audio_seconds: audio.seconds(),
            inference_ms: elapsed * 1000.0,
            real_time_factor: elapsed / audio.seconds(),
            segments,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn wav_boundary_rejects_wrong_rate_channels_and_excessive_duration() {
        let dir = tempfile::tempdir().unwrap();
        for (rate, channels, count, valid) in [
            (16000, 1, 480, true),
            (44100, 1, 480, false),
            (16000, 2, 480, false),
            (16000, 1, 0, false),
            (16000, 1, MAX_SAMPLES + 1, false),
        ] {
            let path = dir.path().join("synthetic.wav");
            let mut writer = hound::WavWriter::create(
                &path,
                hound::WavSpec {
                    channels,
                    sample_rate: rate,
                    bits_per_sample: 16,
                    sample_format: hound::SampleFormat::Int,
                },
            )
            .unwrap();
            for _ in 0..count {
                writer.write_sample(0i16).unwrap();
            }
            writer.finalize().unwrap();
            assert_eq!(Audio::read(&path).is_ok(), valid);
        }
    }
    #[cfg(not(any(feature = "cuda", feature = "vulkan", feature = "metal")))]
    #[test]
    fn gpu_request_fails_before_loading_a_model_on_cpu_build() {
        assert!(matches!(
            Transcriber::new("not-a-model".into(), Device::Gpu).prepare(),
            Err(Error::GpuUnavailable)
        ));
    }
}
