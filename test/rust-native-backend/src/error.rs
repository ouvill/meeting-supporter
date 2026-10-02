use serde::Serialize;
use thiserror::Error;

/// Public, locale-neutral errors. Native diagnostics and local paths stay off the wire.
#[derive(Debug, Error, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SpeechError {
    #[error("whisper_unavailable")]
    WhisperUnavailable,
    #[error("gpu_unavailable")]
    GpuUnavailable,
    #[error("punctuation_load_failed")]
    PunctuationLoadFailed,
    #[error("runtime_unavailable")]
    RuntimeUnavailable,
    #[error("model_load_failed")]
    ModelLoadFailed,
    #[error("invalid_tensor")]
    InvalidTensor,
    #[error("inference_failed")]
    InferenceFailed,
    #[error("invalid_model_output")]
    InvalidModelOutput,
    #[error("invalid_frame_length")]
    InvalidFrameLength,
    #[error("invalid_audio_command")]
    InvalidAudioCommand,
    #[error("invalid_segmentation")]
    InvalidSegmentation,
    #[error("model_not_prepared")]
    ModelNotPrepared,
    #[error("model_files_missing")]
    ModelFilesMissing,
    #[error("invalid_model_path")]
    InvalidModelPath,
    #[error("reazonspeech_not_built")]
    #[cfg(not(feature = "reazonspeech"))]
    ReazonspeechNotBuilt,
    #[error("recognizer_load_failed")]
    #[cfg(feature = "reazonspeech")]
    RecognizerLoadFailed,
    #[error("invalid_recognition_audio")]
    InvalidRecognitionAudio,
    #[error("source_finished")]
    SourceFinished,
    #[error("invalid_request")]
    InvalidRequest,
    #[error("busy")]
    Busy,
}

impl SpeechError {
    pub fn retires_session(&self) -> bool {
        !matches!(
            self,
            Self::InvalidFrameLength | Self::SourceFinished | Self::InvalidAudioCommand
        )
    }
}
