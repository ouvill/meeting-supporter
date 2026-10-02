//! Rust execution of the existing Silero model. No Python interpreter is involved.
use crate::error::SpeechError;
use ort::{session::Session, value::TensorRef};
use std::{collections::VecDeque, path::Path};

const MODEL: &[u8] = include_bytes!("../resources/silero_vad.int8.onnx");
const WINDOW: usize = 512;

pub struct VadState {
    pending: VecDeque<f32>,
    h: [f32; 128],
    c: [f32; 128],
    speech: bool,
    silence_windows: usize,
}

impl VadState {
    pub fn new() -> Self {
        Self {
            pending: VecDeque::with_capacity(WINDOW + 480),
            h: [0.0; 128],
            c: [0.0; 128],
            speech: false,
            silence_windows: 0,
        }
    }
}

pub struct Vad {
    session: Session,
}

impl Vad {
    pub fn load(library: &Path) -> Result<Self, SpeechError> {
        // The caller supplies a trusted runtime library, never a path from an audio request.
        let library = library
            .canonicalize()
            .map_err(|_| SpeechError::RuntimeUnavailable)?;
        ort::init_from(library)
            .map_err(|_| SpeechError::RuntimeUnavailable)?
            .commit();
        let session = (|| -> ort::Result<Session> {
            Session::builder()?
                .with_intra_threads(1)?
                .with_inter_threads(1)?
                .commit_from_memory(MODEL)
        })()
        .map_err(|_| SpeechError::ModelLoadFailed)?;
        Ok(Self { session })
    }

    pub fn process_with_threshold(
        &mut self,
        state: &mut VadState,
        pcm: &[i16],
        threshold: f32,
    ) -> Result<(bool, Vec<f32>), SpeechError> {
        state
            .pending
            .extend(pcm.iter().map(|&sample| f32::from(sample) / 32768.0));
        let mut probabilities = Vec::new();
        while state.pending.len() >= WINDOW {
            let window: Vec<f32> = state.pending.drain(..WINDOW).collect();
            let x = TensorRef::from_array_view(([1usize, WINDOW], window.as_slice()))
                .map_err(|_| SpeechError::InvalidTensor)?;
            let h = TensorRef::from_array_view(([2usize, 1, 64], state.h.as_slice()))
                .map_err(|_| SpeechError::InvalidTensor)?;
            let c = TensorRef::from_array_view(([2usize, 1, 64], state.c.as_slice()))
                .map_err(|_| SpeechError::InvalidTensor)?;
            let output = self
                .session
                .run(ort::inputs!["x" => x, "h" => h, "c" => c])
                .map_err(|_| SpeechError::InferenceFailed)?;
            let (_, probability) = output
                .get("prob")
                .ok_or(SpeechError::InvalidModelOutput)?
                .try_extract_tensor::<f32>()
                .map_err(|_| SpeechError::InvalidModelOutput)?;
            let (h_shape, new_h) = output
                .get("new_h")
                .ok_or(SpeechError::InvalidModelOutput)?
                .try_extract_tensor::<f32>()
                .map_err(|_| SpeechError::InvalidModelOutput)?;
            let (c_shape, new_c) = output
                .get("new_c")
                .ok_or(SpeechError::InvalidModelOutput)?
                .try_extract_tensor::<f32>()
                .map_err(|_| SpeechError::InvalidModelOutput)?;
            if probability.len() != 1
                || !probability[0].is_finite()
                || !(0.0..=1.0).contains(&probability[0])
                || **h_shape != [2, 1, 64]
                || **c_shape != [2, 1, 64]
                || !new_h.iter().chain(new_c).all(|value| value.is_finite())
            {
                return Err(SpeechError::InvalidModelOutput);
            }
            state.h.copy_from_slice(new_h);
            state.c.copy_from_slice(new_c);
            let probability = probability[0];
            if probability >= threshold {
                state.speech = true;
                state.silence_windows = 0;
            } else {
                state.silence_windows = state.silence_windows.saturating_add(1);
                if state.silence_windows >= 4 {
                    state.speech = false;
                }
            }
            probabilities.push(probability);
        }
        Ok((state.speech, probabilities))
    }
}
