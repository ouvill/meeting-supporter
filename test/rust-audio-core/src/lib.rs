//! Experimental ReazonSpeech input preparation; no devices, models or network.
use std::collections::VecDeque;

pub const FRAME_SAMPLES: usize = 480; // 16 kHz, mono, 30 ms
pub const FRAME_BYTES: usize = FRAME_SAMPLES * 2;
pub const PREROLL_FRAMES: usize = 5;
pub const MAX_SEGMENT_FRAMES: usize = 934; // (30 - 2 * 0.9)s, less preroll and one frame

#[derive(Clone, Copy, Debug)]
pub struct Config {
    pub silence_seconds: f64,
    pub min_voiced_ms: usize,
    pub min_voiced_ratio: f64,
    pub min_rms_dbfs: f64,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            silence_seconds: 0.4,
            min_voiced_ms: 240,
            min_voiced_ratio: 0.35,
            min_rms_dbfs: -45.0,
        }
    }
}

#[derive(Debug)]
pub struct Segment {
    /// Normalized audio, including preroll, ready for the existing recognizer.
    pub audio: Vec<f32>,
    pub voiced_frames: usize,
    pub segment_frames: usize,
    pub rms_dbfs: f64,
    pub accepted: bool,
}

pub struct Segmenter {
    config: Config,
    silence_threshold: usize,
    preroll: VecDeque<[u8; FRAME_BYTES]>,
    pcm: Vec<u8>,
    prepended_samples: usize,
    voiced_frames: usize,
    segment_frames: usize,
    silence_frames: usize,
}

impl Segmenter {
    pub fn new(config: Config) -> Result<Self, &'static str> {
        if !config.silence_seconds.is_finite()
            || config.silence_seconds < 0.0
            || !config.min_voiced_ratio.is_finite()
            || !(0.0..=1.0).contains(&config.min_voiced_ratio)
            || !config.min_rms_dbfs.is_finite()
        {
            return Err("invalid segmentation configuration");
        }
        Ok(Self {
            config,
            silence_threshold: ((config.silence_seconds * 1000.0 / 30.0) as usize).max(1),
            preroll: VecDeque::with_capacity(PREROLL_FRAMES),
            pcm: Vec::new(),
            prepended_samples: 0,
            voiced_frames: 0,
            segment_frames: 0,
            silence_frames: 0,
        })
    }

    /// Invalid frames leave the state unchanged. VAD is supplied by the caller.
    pub fn push(&mut self, pcm: &[u8], is_speech: bool) -> Result<Option<Segment>, &'static str> {
        let frame: &[u8; FRAME_BYTES] =
            pcm.try_into().map_err(|_| "expected 30 ms PCM16LE frame")?;
        if is_speech {
            if self.segment_frames == 0 {
                for previous in &self.preroll {
                    self.pcm.extend_from_slice(previous);
                }
                self.prepended_samples = self.pcm.len() / 2;
            }
            self.voiced_frames += 1;
            self.silence_frames = 0;
        } else if self.segment_frames > 0 {
            self.silence_frames += 1;
        }
        if is_speech || self.segment_frames > 0 {
            self.pcm.extend_from_slice(frame);
            self.segment_frames += 1;
        }
        let segment = if self.segment_frames > 0
            && (self.silence_frames >= self.silence_threshold
                || self.segment_frames >= MAX_SEGMENT_FRAMES)
        {
            self.finish()
        } else {
            None
        };
        if self.preroll.len() == PREROLL_FRAMES {
            self.preroll.pop_front();
        }
        self.preroll.push_back(*frame);
        Ok(segment)
    }

    /// Flush at end of stream, matching the Python sentinel (not cancellation).
    pub fn finish(&mut self) -> Option<Segment> {
        if self.segment_frames == 0 {
            return None;
        }
        let audio: Vec<f32> = self
            .pcm
            .as_chunks::<2>()
            .0
            .iter()
            .map(|bytes| f32::from(i16::from_le_bytes([bytes[0], bytes[1]])) / 32768.0)
            .collect();
        // Exclude preroll from evidence, but keep it in recognition input.
        // f64 accumulation is deliberate; NumPy's float32 dot may differ near a threshold.
        let samples = &audio[self.prepended_samples..];
        let sum: f64 = samples.iter().map(|&value| f64::from(value).powi(2)).sum();
        let rms_dbfs = 20.0 * (sum / samples.len().max(1) as f64).sqrt().max(1e-6).log10();
        let accepted = self.voiced_frames * 30 >= self.config.min_voiced_ms
            && self.voiced_frames as f64 / self.segment_frames as f64
                >= self.config.min_voiced_ratio
            && rms_dbfs >= self.config.min_rms_dbfs;
        let segment = Segment {
            audio,
            voiced_frames: self.voiced_frames,
            segment_frames: self.segment_frames,
            rms_dbfs,
            accepted,
        };
        self.pcm.clear();
        self.prepended_samples = 0;
        self.voiced_frames = 0;
        self.segment_frames = 0;
        self.silence_frames = 0;
        Some(segment)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(value: i16) -> Vec<u8> {
        value.to_le_bytes().repeat(FRAME_SAMPLES)
    }

    #[test]
    fn preroll_is_excluded_from_gate_and_kept_in_audio() {
        let mut core = Segmenter::new(Config::default()).unwrap();
        for _ in 0..8 {
            core.push(&frame(i16::MAX), false).unwrap();
        }
        for _ in 0..8 {
            core.push(&frame(0), true).unwrap();
        }
        let result = core.finish().unwrap();
        assert!(!result.accepted);
        assert_eq!(result.rms_dbfs, -120.0);
        assert_eq!(result.audio.len(), 13 * FRAME_SAMPLES);
        assert!(core.finish().is_none());
    }

    #[test]
    fn silence_boundary_and_signed_pcm() {
        let mut core = Segmenter::new(Config::default()).unwrap();
        for _ in 0..8 {
            assert!(core.push(&frame(i16::MIN), true).unwrap().is_none());
        }
        for _ in 0..12 {
            assert!(core.push(&frame(0), false).unwrap().is_none());
        }
        let result = core.push(&frame(0), false).unwrap().unwrap();
        assert!(result.accepted);
        assert_eq!(result.segment_frames, 21);
        assert_eq!(result.audio[0], -1.0);
    }

    #[test]
    fn continuous_speech_splits_before_model_limit() {
        let mut core = Segmenter::new(Config::default()).unwrap();
        for _ in 0..MAX_SEGMENT_FRAMES - 1 {
            assert!(core.push(&frame(1000), true).unwrap().is_none());
        }
        let result = core.push(&frame(1000), true).unwrap().unwrap();
        assert_eq!(result.segment_frames, MAX_SEGMENT_FRAMES);
        core.push(&frame(1000), true).unwrap();
        assert_eq!(core.finish().unwrap().audio.len(), 6 * FRAME_SAMPLES);
    }

    #[test]
    fn rejects_invalid_boundaries_without_advancing_state() {
        assert!(
            Segmenter::new(Config {
                min_rms_dbfs: f64::NAN,
                ..Config::default()
            })
            .is_err()
        );
        let mut core = Segmenter::new(Config::default()).unwrap();
        assert!(core.push(&[0], true).is_err());
        assert!(core.finish().is_none());
    }
}
