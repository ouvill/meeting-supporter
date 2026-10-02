//! whisper.cpp inference. Only fixed device diagnostics are consumed; native logs
//! (which can contain local paths) never cross the worker protocol.
use crate::error::SpeechError;
use std::path::Path;

pub const fn gpu_supported() -> bool {
    cfg!(all(
        feature = "whisper",
        any(feature = "cuda", feature = "vulkan", feature = "metal")
    ))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Device {
    Auto,
    Cpu,
    Gpu,
}
#[derive(Clone, Copy, Debug)]
pub enum Language {
    Ja,
    En,
    Auto,
}

#[cfg(feature = "whisper")]
mod implementation {
    use super::*;
    use std::{
        ffi::{CStr, c_char, c_void},
        sync::{
            Mutex,
            atomic::{AtomicBool, Ordering},
        },
    };
    use whisper_rs::{
        FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters, WhisperState,
    };
    static LOAD: Mutex<()> = Mutex::new(());
    static GPU: AtomicBool = AtomicBool::new(false);

    // Pinned whisper.cpp 1.8.3 reports GPU selection and initialization failure
    // here. Keep the adapter conservative: unrecognized diagnostics mean CPU.
    unsafe extern "C" fn log(
        _: whisper_rs::whisper_rs_sys::ggml_log_level,
        text: *const c_char,
        _: *mut c_void,
    ) {
        if text.is_null() {
            return;
        }
        // SAFETY: whisper.cpp supplies a NUL-terminated string valid for the callback.
        let bytes = unsafe { CStr::from_ptr(text) }.to_bytes();
        if bytes.starts_with(b"whisper_backend_init_gpu: using ") {
            GPU.store(true, Ordering::Relaxed);
        } else if bytes.starts_with(b"whisper_backend_init_gpu: no GPU found")
            || bytes.starts_with(b"whisper_backend_init_gpu: failed to initialize ")
        {
            GPU.store(false, Ordering::Relaxed);
        }
    }
    pub struct Whisper {
        state: WhisperState,
        language: Language,
        device: &'static str,
    }
    impl Whisper {
        pub fn load(path: &Path, device: Device, language: Language) -> Result<Self, SpeechError> {
            let _guard = LOAD.lock().map_err(|_| SpeechError::ModelLoadFailed)?;
            // SAFETY: callback uses static atomics, no borrowed user data, and never panics.
            unsafe {
                whisper_rs::set_log_callback(Some(log), std::ptr::null_mut());
            }
            let gpu_build = gpu_supported();
            if device == Device::Gpu && !gpu_build {
                return Err(SpeechError::GpuUnavailable);
            }
            let use_gpu = device != Device::Cpu && gpu_build;
            let load = |gpu| {
                GPU.store(false, Ordering::Relaxed);
                let mut parameters = WhisperContextParameters::default();
                parameters.use_gpu(gpu);
                let context = WhisperContext::new_with_params(
                    path.to_str().ok_or(SpeechError::InvalidModelPath)?,
                    parameters,
                )
                .map_err(|_| SpeechError::ModelLoadFailed)?;
                context
                    .create_state()
                    .map_err(|_| SpeechError::ModelLoadFailed)
            };
            let state = match load(use_gpu) {
                Ok(state) => state,
                Err(_) if device == Device::Auto && use_gpu => load(false)?,
                Err(error) => return Err(error),
            };
            let actual_gpu = use_gpu && GPU.load(Ordering::Relaxed);
            if device == Device::Gpu && !actual_gpu {
                return Err(SpeechError::GpuUnavailable);
            }
            let mut model = Self {
                state,
                language,
                device: if actual_gpu { "gpu" } else { "cpu" },
            };
            // Loading weights does not execute the inference graphs. In particular,
            // Vulkan compiles needed shaders lazily on the first graph execution.
            // Exercise the same encoder/decoder path before reporting Prepared, while
            // capture is not yet queued for recognition. Keep this state for reuse.
            // Bypass VAD (silence would be rejected) and discard any generated text;
            // no_context and whisper_full's result reset isolate the next utterance.
            let _ = model.transcribe(&[0.0; 16_000])?;
            Ok(model)
        }
        pub fn device(&self) -> &'static str {
            self.device
        }
        pub fn transcribe(&mut self, audio: &[f32]) -> Result<String, SpeechError> {
            if audio.is_empty() || audio.len() > 30 * 16000 || audio.iter().any(|s| !s.is_finite())
            {
                return Err(SpeechError::InvalidRecognitionAudio);
            }
            let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
            let threads = std::thread::available_parallelism()
                .map_or(2, usize::from)
                .clamp(1, 4);
            params.set_n_threads(threads as i32);
            params.set_language(match self.language {
                Language::Ja => Some("ja"),
                Language::En => Some("en"),
                Language::Auto => None,
            });
            params.set_translate(false);
            params.set_no_context(true);
            params.set_temperature(0.0);
            params.set_temperature_inc(0.0);
            params.set_print_special(false);
            params.set_print_progress(false);
            params.set_print_realtime(false);
            params.set_print_timestamps(false);
            self.state
                .full(params, audio)
                .map_err(|_| SpeechError::InferenceFailed)?;
            let text: String = self
                .state
                .as_iter()
                .map(|segment| segment.to_string())
                .collect();
            if text.len() > 16384 {
                return Err(SpeechError::InvalidModelOutput);
            }
            Ok(text.trim().to_owned())
        }
    }
    #[cfg(test)]
    mod tests {
        use super::*;
        #[test]
        fn explicit_gpu_rejects_cpu_only_build_before_opening_model() {
            if !cfg!(any(feature = "cuda", feature = "vulkan", feature = "metal")) {
                assert!(matches!(
                    Whisper::load(Path::new("missing"), Device::Gpu, Language::Ja),
                    Err(SpeechError::GpuUnavailable)
                ));
            }
        }
        #[test]
        fn device_diagnostics_detect_native_fallback() {
            let _guard = LOAD.lock().unwrap();
            unsafe {
                log(
                    0,
                    c"whisper_backend_init_gpu: using Vulkan0 backend\n".as_ptr(),
                    std::ptr::null_mut(),
                );
                assert!(GPU.load(Ordering::Relaxed));
                log(
                    0,
                    c"whisper_backend_init_gpu: failed to initialize Vulkan0 backend\n".as_ptr(),
                    std::ptr::null_mut(),
                );
                assert!(!GPU.load(Ordering::Relaxed));
            }
        }
    }
}
#[cfg(feature = "whisper")]
pub use implementation::Whisper;

#[cfg(not(feature = "whisper"))]
pub struct Whisper;
#[cfg(not(feature = "whisper"))]
impl Whisper {
    pub fn load(_: &Path, _: Device, _: Language) -> Result<Self, SpeechError> {
        Err(SpeechError::WhisperUnavailable)
    }
    pub fn device(&self) -> &'static str {
        "cpu"
    }
    pub fn transcribe(&mut self, _: &[f32]) -> Result<String, SpeechError> {
        Err(SpeechError::WhisperUnavailable)
    }
}
