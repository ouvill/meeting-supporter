//! ReazonSpeech K2-v2, using the same CPU greedy decoder and padding as Python.
use crate::error::SpeechError;
use std::path::Path;

pub const MODEL_FILES: [&str; 4] = [
    "tokens.txt",
    "encoder-epoch-99-avg-1.int8.onnx",
    "decoder-epoch-99-avg-1.int8.onnx",
    "joiner-epoch-99-avg-1.int8.onnx",
];
const PADDING: usize = 14_400;
const MAX_SAMPLES: usize = 480_000 - 2 * PADDING;

pub struct Reazon {
    #[cfg(feature = "reazonspeech")]
    recognizer: sherpa_onnx::OfflineRecognizer,
}

impl Reazon {
    pub fn load(directory: &Path) -> Result<Self, SpeechError> {
        let mut paths = Vec::new();
        for file in MODEL_FILES {
            let path = directory.join(file);
            if !path.is_file() {
                return Err(SpeechError::ModelFilesMissing);
            }
            let path = path.to_str().ok_or(SpeechError::InvalidModelPath)?;
            if path.contains('\0') {
                return Err(SpeechError::InvalidModelPath);
            }
            paths.push(path.to_owned());
        }
        #[cfg(feature = "reazonspeech")]
        {
            use sherpa_onnx::{
                OfflineModelConfig, OfflineRecognizer, OfflineRecognizerConfig,
                OfflineTransducerModelConfig,
            };
            let config = OfflineRecognizerConfig {
                model_config: OfflineModelConfig {
                    tokens: Some(paths[0].clone()),
                    transducer: OfflineTransducerModelConfig {
                        encoder: Some(paths[1].clone()),
                        decoder: Some(paths[2].clone()),
                        joiner: Some(paths[3].clone()),
                    },
                    num_threads: 1,
                    provider: Some("cpu".into()),
                    ..Default::default()
                },
                decoding_method: Some("greedy_search".into()),
                ..Default::default()
            };
            let recognizer =
                OfflineRecognizer::create(&config).ok_or(SpeechError::RecognizerLoadFailed)?;
            Ok(Self { recognizer })
        }
        #[cfg(not(feature = "reazonspeech"))]
        Err(SpeechError::ReazonspeechNotBuilt)
    }

    pub fn transcribe(&mut self, audio: &[f32]) -> Result<String, SpeechError> {
        let padded = padded_audio(audio)?;
        #[cfg(feature = "reazonspeech")]
        {
            let stream = self.recognizer.create_stream();
            stream.accept_waveform(16_000, &padded);
            self.recognizer.decode(&stream);
            let result = stream.get_result().ok_or(SpeechError::InvalidModelOutput)?;
            Ok(result.text.trim().to_owned())
        }
        #[cfg(not(feature = "reazonspeech"))]
        {
            let _ = padded;
            Err(SpeechError::ReazonspeechNotBuilt)
        }
    }
}

fn padded_audio(audio: &[f32]) -> Result<Vec<f32>, SpeechError> {
    if audio.is_empty()
        || audio.len() > MAX_SAMPLES
        || audio
            .iter()
            .any(|x| !x.is_finite() || !(-1.0..=1.0).contains(x))
    {
        return Err(SpeechError::InvalidRecognitionAudio);
    }
    let mut padded = vec![0.0; audio.len() + 2 * PADDING];
    padded[PADDING..PADDING + audio.len()].copy_from_slice(audio);
    Ok(padded)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn padding_preserves_samples_and_model_limit() {
        let padded = padded_audio(&[0.25, -0.5]).unwrap();
        assert_eq!(padded.len(), 2 * PADDING + 2);
        assert_eq!(&padded[PADDING..PADDING + 2], &[0.25, -0.5]);
        assert!(padded[..PADDING].iter().all(|&x| x == 0.0));
        assert!(padded[PADDING + 2..].iter().all(|&x| x == 0.0));
        for invalid in [
            vec![],
            vec![f32::NAN],
            vec![1.1],
            vec![0.0; MAX_SAMPLES + 1],
        ] {
            assert_eq!(
                padded_audio(&invalid),
                Err(SpeechError::InvalidRecognitionAudio)
            );
        }
    }
}
