//! CPAL capture owns a bounded SPSC producer; inference runs on the calling thread.
use crate::{
    protocol::{Reply, Role},
    session::{SessionConfig, SpeechSession},
    wav::emit_segment,
};
use cpal::{
    Sample, SampleFormat, SizedSample,
    traits::{DeviceTrait, HostTrait, StreamTrait},
};
use ringbuf::{HeapProd, HeapRb, traits::*};
use rubato::{
    Resampler, SincFixedIn, SincInterpolationParameters, SincInterpolationType, WindowFunction,
};
use serde::{Deserialize, Serialize};
use std::{
    io::{self, BufRead, Read, Write},
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU8, Ordering},
    },
    thread,
    time::{Duration, Instant},
};
use thiserror::Error;

const OUTPUT_RATE: usize = 16_000;
const FRAME: usize = 480;
const BUFFER_SECONDS: usize = 5;
const STREAM_FAILED: u8 = 1;
const OVERFLOW: u8 = 2;
const INVALID_SAMPLES: u8 = 3;

#[derive(Debug, Error)]
pub enum MicrophoneError {
    #[error("input_device_unavailable: use --list-input-devices and check microphone permissions")]
    DeviceUnavailable,
    #[error("input_config_unsupported")]
    UnsupportedConfig,
    #[error("input_stream_failed: check microphone permissions and device connection")]
    StreamFailed,
    #[error("capture_buffer_overflow: inference could not keep up; restart capture")]
    Overflow,
    #[error("invalid_capture_samples")]
    InvalidSamples,
    #[error("resampling_failed")]
    Resampling,
    #[error("signal_handler_unavailable")]
    SignalHandler,
    #[error(transparent)]
    Speech(#[from] crate::error::SpeechError),
    #[error("output_failed")]
    Output(#[source] io::Error),
}

#[derive(Serialize)]
struct InputDevice {
    index: usize,
    name: String,
}

pub fn list_devices() -> Result<(), MicrophoneError> {
    let devices = cpal::default_host()
        .input_devices()
        .map_err(|_| MicrophoneError::DeviceUnavailable)?;
    let mut output = io::stdout().lock();
    for (index, device) in devices.enumerate() {
        let name = device
            .name()
            .map_err(|_| MicrophoneError::DeviceUnavailable)?;
        serde_json::to_writer(&mut output, &InputDevice { index, name })
            .map_err(io::Error::other)
            .map_err(MicrophoneError::Output)?;
        use io::Write;
        output.write_all(b"\n").map_err(MicrophoneError::Output)?;
    }
    Ok(())
}

fn device(index: Option<usize>) -> Result<cpal::Device, MicrophoneError> {
    let host = cpal::default_host();
    match index {
        Some(index) => host
            .input_devices()
            .map_err(|_| MicrophoneError::DeviceUnavailable)?
            .nth(index),
        None => host.default_input_device(),
    }
    .ok_or(MicrophoneError::DeviceUnavailable)
}

/// The process-level CLI installs one Ctrl+C handler. A second interrupt forces exit.
pub fn transcribe(
    config: SessionConfig,
    index: Option<usize>,
    seconds: Option<u32>,
    desktop: bool,
) -> Result<(), MicrophoneError> {
    let stopped = Arc::new(AtomicBool::new(false));
    let signal = Arc::clone(&stopped);
    let mut interrupted = false;
    ctrlc::set_handler(move || {
        if interrupted {
            std::process::exit(130);
        }
        interrupted = true;
        signal.store(true, Ordering::SeqCst);
    })
    .map_err(|_| MicrophoneError::SignalHandler)?;
    if desktop {
        // A private pipe belongs to one desktop session. EOF also stops capture.
        let control = Arc::clone(&stopped);
        thread::spawn(move || {
            let mut line = String::new();
            let _ = io::stdin().lock().take(257).read_line(&mut line);
            let _ = serde_json::from_str::<DesktopCommand>(&line);
            // Invalid control input fails closed: never keep recording.
            control.store(true, Ordering::SeqCst);
        });
        desktop_event(serde_json::json!({"type": "ready", "protocol": 1}))?;
    }
    let result = capture_session(config, index, seconds, stopped, desktop);
    if desktop {
        match &result {
            Ok(()) => desktop_event(serde_json::json!({"type": "stopped"}))?,
            Err(error) => {
                let code = match error {
                    MicrophoneError::DeviceUnavailable | MicrophoneError::UnsupportedConfig => {
                        "microphone_unavailable"
                    }
                    MicrophoneError::Speech(_) => "model_unavailable",
                    MicrophoneError::Overflow => "capture_overflow",
                    _ => "capture_failed",
                };
                desktop_event(serde_json::json!({"type": "error", "code": code}))?;
            }
        }
    }
    result
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum DesktopCommand {
    Stop {},
}

fn desktop_event(value: serde_json::Value) -> Result<(), MicrophoneError> {
    let mut output = io::stdout().lock();
    serde_json::to_writer(&mut output, &value)
        .map_err(io::Error::other)
        .map_err(MicrophoneError::Output)?;
    output.write_all(b"\n").map_err(MicrophoneError::Output)?;
    output.flush().map_err(MicrophoneError::Output)
}

fn capture_session(
    config: SessionConfig,
    index: Option<usize>,
    seconds: Option<u32>,
    stopped: Arc<AtomicBool>,
    desktop: bool,
) -> Result<(), MicrophoneError> {
    let device = device(index)?;
    let supported = device
        .default_input_config()
        .map_err(|_| MicrophoneError::UnsupportedConfig)?;
    let rate = supported.sample_rate().0 as usize;
    let channels = supported.channels() as usize;
    if !(8_000..=192_000).contains(&rate) || !(1..=32).contains(&channels) {
        return Err(MicrophoneError::UnsupportedConfig);
    }
    let mut converter = Converter::new(rate)?;
    eprintln!("Preparing Silero and ReazonSpeech...");
    let mut session = SpeechSession::new(config).prepare()?;
    if stopped.load(Ordering::SeqCst) {
        return Ok(());
    }
    let (producer, mut consumer) = HeapRb::<f32>::new(rate * BUFFER_SECONDS).split();
    let failed = Arc::new(AtomicU8::new(0));
    let control = CaptureControl {
        failed: Arc::clone(&failed),
        stopped: Arc::clone(&stopped),
        deadline: seconds.map(|s| Instant::now() + Duration::from_secs(s.into())),
    };
    let stream_config: cpal::StreamConfig = supported.clone().into();
    let stream = match supported.sample_format() {
        SampleFormat::I8 => stream::<i8>(&device, &stream_config, producer, &control),
        SampleFormat::I16 => stream::<i16>(&device, &stream_config, producer, &control),
        SampleFormat::I32 => stream::<i32>(&device, &stream_config, producer, &control),
        SampleFormat::I64 => stream::<i64>(&device, &stream_config, producer, &control),
        SampleFormat::U8 => stream::<u8>(&device, &stream_config, producer, &control),
        SampleFormat::U16 => stream::<u16>(&device, &stream_config, producer, &control),
        SampleFormat::U32 => stream::<u32>(&device, &stream_config, producer, &control),
        SampleFormat::U64 => stream::<u64>(&device, &stream_config, producer, &control),
        SampleFormat::F32 => stream::<f32>(&device, &stream_config, producer, &control),
        SampleFormat::F64 => stream::<f64>(&device, &stream_config, producer, &control),
        _ => Err(MicrophoneError::UnsupportedConfig),
    }?;
    stream.play().map_err(|_| MicrophoneError::StreamFailed)?;
    eprintln!(
        "Microphone active ({rate} Hz, {channels} channels). Ctrl+C to finish; twice to force exit."
    );
    if desktop {
        desktop_event(serde_json::json!({"type": "listening"}))?;
    }
    let mut stream = Some(stream);
    let mut output = io::stdout().lock();
    let mut progress = Instant::now();
    let mut emit = |frame: &[i16; FRAME]| -> Result<(), MicrophoneError> {
        emit_capture(&mut output, session.audio(Role::User, frame)?, desktop)?;
        // Reports inference-thread progress, not merely a responsive control thread.
        if desktop && progress.elapsed() >= Duration::from_millis(500) {
            output
                .write_all(b"{\"type\":\"progress\"}\n")
                .map_err(MicrophoneError::Output)?;
            output.flush().map_err(MicrophoneError::Output)?;
            progress = Instant::now();
        }
        Ok(())
    };
    loop {
        check_failure(&failed)?;
        if control.should_stop() {
            // Dropping CPAL stops callbacks before draining the finite buffer.
            drop(stream.take());
        }
        if let Some(sample) = consumer.try_pop() {
            converter.push(sample, &mut emit)?;
        } else if stream.is_none() {
            break;
        } else {
            thread::sleep(Duration::from_millis(5));
        }
    }
    check_failure(&failed)?;
    converter.finish(&mut emit)?;
    emit_capture(&mut output, session.finish(Role::User)?, desktop)?;
    eprintln!("Microphone stopped; accepted audio flushed.");
    Ok(())
}

fn emit_capture(
    output: &mut impl Write,
    reply: Reply,
    desktop: bool,
) -> Result<(), MicrophoneError> {
    if !desktop {
        return emit_segment(output, reply).map_err(MicrophoneError::Output);
    }
    if let Reply::Audio {
        segment: Some(segment),
        ..
    }
    | Reply::Finished {
        segment: Some(segment),
    } = reply
    {
        serde_json::to_writer(
            &mut *output,
            &serde_json::json!({"type":"segment", "segment":segment}),
        )
        .map_err(io::Error::other)
        .map_err(MicrophoneError::Output)?;
        output.write_all(b"\n").map_err(MicrophoneError::Output)?;
        output.flush().map_err(MicrophoneError::Output)?;
    }
    Ok(())
}

fn check_failure(failed: &AtomicU8) -> Result<(), MicrophoneError> {
    match failed.load(Ordering::Acquire) {
        0 => Ok(()),
        OVERFLOW => Err(MicrophoneError::Overflow),
        INVALID_SAMPLES => Err(MicrophoneError::InvalidSamples),
        _ => Err(MicrophoneError::StreamFailed),
    }
}

#[derive(Clone)]
struct CaptureControl {
    failed: Arc<AtomicU8>,
    stopped: Arc<AtomicBool>,
    deadline: Option<Instant>,
}

impl CaptureControl {
    fn should_stop(&self) -> bool {
        if self
            .deadline
            .is_some_and(|deadline| Instant::now() >= deadline)
        {
            self.stopped.store(true, Ordering::SeqCst);
        }
        self.stopped.load(Ordering::SeqCst)
    }
}

fn stream<T>(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    mut producer: HeapProd<f32>,
    control: &CaptureControl,
) -> Result<cpal::Stream, MicrophoneError>
where
    T: SizedSample,
    f32: cpal::FromSample<T>,
{
    let channels = config.channels as usize;
    let control = control.clone();
    let error_failed = Arc::clone(&control.failed);
    device
        .build_input_stream(
            config,
            move |data: &[T], _| {
                if !control.should_stop() {
                    capture(data, channels, &mut producer, &control.failed);
                }
            },
            move |_| {
                let _ = error_failed.compare_exchange(
                    0,
                    STREAM_FAILED,
                    Ordering::AcqRel,
                    Ordering::Acquire,
                );
            },
            None,
        )
        .map_err(|_| MicrophoneError::StreamFailed)
}

// No allocations, locks, logging, resampling or inference on the audio callback.
fn capture<T>(data: &[T], channels: usize, producer: &mut HeapProd<f32>, failed: &AtomicU8)
where
    T: Sample,
    f32: cpal::FromSample<T>,
{
    if failed.load(Ordering::Acquire) != 0 {
        return;
    }
    if channels == 0 || !data.len().is_multiple_of(channels) {
        let _ = failed.compare_exchange(0, INVALID_SAMPLES, Ordering::AcqRel, Ordering::Acquire);
        return;
    }
    for frame in data.chunks_exact(channels) {
        let mut mono = 0.0;
        for sample in frame {
            let sample = sample.to_sample::<f32>();
            if !sample.is_finite() {
                let _ = failed.compare_exchange(
                    0,
                    INVALID_SAMPLES,
                    Ordering::AcqRel,
                    Ordering::Acquire,
                );
                return;
            }
            mono += sample.clamp(-1.0, 1.0) / channels as f32;
        }
        if producer.try_push(mono).is_err() {
            let _ = failed.compare_exchange(0, OVERFLOW, Ordering::AcqRel, Ordering::Acquire);
            return;
        }
    }
}

/// Stateful anti-aliased conversion. SincFixedIn withholds look-ahead at the end
/// of a block rather than emitting leading silence. Do not skip output_delay()
/// samples: doing so would discard real input. Finish supplies the look-ahead.
struct Converter {
    resampler: SincFixedIn<f32>,
    input: Vec<f32>,
    output: Vec<Vec<f32>>,
    input_count: usize,
    input_total: u64,
    output_total: u64,
    rate: usize,
    frame: [i16; FRAME],
    frame_count: usize,
}

impl Converter {
    fn new(rate: usize) -> Result<Self, MicrophoneError> {
        if !(8_000..=192_000).contains(&rate) {
            return Err(MicrophoneError::UnsupportedConfig);
        }
        // Sinc interpolation supplies anti-alias filtering without FFT dependencies.
        let resampler = SincFixedIn::<f32>::new(
            OUTPUT_RATE as f64 / rate as f64,
            1.0,
            SincInterpolationParameters {
                sinc_len: 256,
                f_cutoff: 0.95,
                oversampling_factor: 128,
                interpolation: SincInterpolationType::Linear,
                window: WindowFunction::BlackmanHarris2,
            },
            1024,
            1,
        )
        .map_err(|_| MicrophoneError::Resampling)?;
        let input = vec![0.0; resampler.input_frames_next()];
        let output = resampler.output_buffer_allocate(true);
        Ok(Self {
            resampler,
            input,
            output,
            input_count: 0,
            input_total: 0,
            output_total: 0,
            rate,
            frame: [0; FRAME],
            frame_count: 0,
        })
    }

    fn push(
        &mut self,
        sample: f32,
        emit: &mut impl FnMut(&[i16; FRAME]) -> Result<(), MicrophoneError>,
    ) -> Result<(), MicrophoneError> {
        if !sample.is_finite() {
            return Err(MicrophoneError::InvalidSamples);
        }
        self.input[self.input_count] = sample;
        self.input_count += 1;
        self.input_total += 1;
        if self.input_count == self.input.len() {
            self.process(emit)?;
        }
        Ok(())
    }

    fn process(
        &mut self,
        emit: &mut impl FnMut(&[i16; FRAME]) -> Result<(), MicrophoneError>,
    ) -> Result<(), MicrophoneError> {
        let (_, written) = self
            .resampler
            .process_into_buffer(&[self.input.as_slice()], &mut self.output, None)
            .map_err(|_| MicrophoneError::Resampling)?;
        let target = self.input_total * OUTPUT_RATE as u64 / self.rate as u64;
        for index in 0..written {
            if self.output_total == target {
                break;
            }
            let sample = self.output[0][index];
            self.frame[self.frame_count] = (sample.clamp(-1.0, 1.0) * 32768.0)
                .round()
                .clamp(-32768.0, 32767.0) as i16;
            self.frame_count += 1;
            self.output_total += 1;
            if self.frame_count == FRAME {
                emit(&self.frame)?;
                self.frame_count = 0;
            }
        }
        self.input_count = 0;
        Ok(())
    }

    fn finish(
        mut self,
        emit: &mut impl FnMut(&[i16; FRAME]) -> Result<(), MicrophoneError>,
    ) -> Result<(), MicrophoneError> {
        let target = self.input_total * OUTPUT_RATE as u64 / self.rate as u64;
        while self.output_total < target {
            self.input[self.input_count..].fill(0.0);
            self.process(emit)?;
        }
        if self.frame_count > 0 {
            self.frame[self.frame_count..].fill(0);
            emit(&self.frame)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stop_request_and_deadline_do_not_depend_on_inference_progress() {
        let mut control = CaptureControl {
            failed: Arc::new(AtomicU8::new(0)),
            stopped: Arc::new(AtomicBool::new(false)),
            deadline: None,
        };
        assert!(!control.should_stop());
        control.stopped.store(true, Ordering::SeqCst);
        assert!(control.should_stop());
        control.stopped.store(false, Ordering::SeqCst);
        control.deadline = Some(Instant::now());
        assert!(control.should_stop());
        assert!(control.stopped.load(Ordering::SeqCst));
    }

    #[cfg(feature = "reazonspeech")]
    #[test]
    #[ignore = "requires pinned models and a locally synthesized 48 kHz WAV; see README"]
    fn synthetic_capture_through_resampling_and_real_recognition() {
        use crate::{
            protocol::{Recognition, Reply},
            session::SpeechPlan,
        };
        let required =
            |name| std::env::var_os(name).expect("set synthetic test artifact variables");
        let mut wav = hound::WavReader::open(required("MEETING_TEST_SYNTHETIC_WAV")).unwrap();
        assert_eq!(wav.spec().sample_rate, 48_000);
        assert_eq!(wav.spec().channels, 1);
        let mut session = SpeechSession::new(SessionConfig {
            runtime_library: required("MEETING_TEST_ORT_LIBRARY").into(),
            plan: SpeechPlan::SileroReazon {
                punctuation_directory: None,
                model_directory: required("MEETING_TEST_REAZON_MODEL").into(),
            },
        })
        .prepare()
        .unwrap();
        let mut texts = Vec::new();
        let mut collect = |reply| {
            if let Reply::Audio {
                segment: Some(segment),
                ..
            }
            | Reply::Finished {
                segment: Some(segment),
            } = reply
                && let Recognition::Recognized { text, .. } = segment.recognition
            {
                texts.push(text);
            }
        };
        let mut emit = |frame: &[i16; FRAME]| {
            collect(session.audio(Role::User, frame)?);
            Ok(())
        };
        let (mut producer, mut consumer) = HeapRb::<f32>::new(2048).split();
        let failed = AtomicU8::new(0);
        let mut converter = Converter::new(48_000).unwrap();
        let mut interleaved = Vec::with_capacity(1920);
        for sample in wav.samples::<i16>() {
            let sample = sample.unwrap();
            interleaved.extend_from_slice(&[sample, sample]);
            if interleaved.len() == 1920 {
                capture(&interleaved, 2, &mut producer, &failed);
                while let Some(sample) = consumer.try_pop() {
                    converter.push(sample, &mut emit).unwrap();
                }
                interleaved.clear();
            }
        }
        capture(&interleaved, 2, &mut producer, &failed);
        while let Some(sample) = consumer.try_pop() {
            converter.push(sample, &mut emit).unwrap();
        }
        converter.finish(&mut emit).unwrap();
        check_failure(&failed).unwrap();
        collect(session.finish(Role::User).unwrap());
        assert!(texts.iter().any(|text| text.contains("音声認識")));
        assert!(texts.iter().any(|text| text.contains("会議")));
    }

    #[test]
    fn callback_normalizes_mixes_and_stops_after_overflow() {
        let (mut producer, mut consumer) = HeapRb::<f32>::new(2).split();
        let failed = AtomicU8::new(0);
        capture(
            &[i16::MIN, i16::MAX, 16384, 16384],
            2,
            &mut producer,
            &failed,
        );
        assert!(consumer.try_pop().unwrap().abs() < 0.0001);
        assert_eq!(consumer.try_pop(), Some(0.5));
        capture(&[0u16, 32768, 65535], 1, &mut producer, &failed);
        assert!(matches!(
            check_failure(&failed),
            Err(MicrophoneError::Overflow)
        ));
        assert_eq!(consumer.try_pop(), Some(-1.0));
        assert_eq!(consumer.try_pop(), Some(0.0));
        capture(&[0.5f32], 1, &mut producer, &failed);
        assert!(consumer.try_pop().is_none());
    }

    #[test]
    fn invalid_samples_fail_without_entering_the_buffer() {
        let (mut producer, mut consumer) = HeapRb::<f32>::new(8).split();
        let failed = AtomicU8::new(0);
        capture(&[f32::NAN], 1, &mut producer, &failed);
        assert!(matches!(
            check_failure(&failed),
            Err(MicrophoneError::InvalidSamples)
        ));
        assert!(consumer.try_pop().is_none());
    }

    fn tone(rate: usize, frequency: f32, length: usize) -> Vec<i16> {
        let mut converter = Converter::new(rate).unwrap();
        let mut result = Vec::new();
        let mut emit = |frame: &[i16; FRAME]| {
            result.extend_from_slice(frame);
            Ok(())
        };
        for index in 0..length {
            converter
                .push(
                    0.5 * (std::f32::consts::TAU * frequency * index as f32 / rate as f32).sin(),
                    &mut emit,
                )
                .unwrap();
        }
        converter.finish(&mut emit).unwrap();
        result
    }

    #[test]
    fn resampling_preserves_duration_tone_and_zero_pads_only_final_frame() {
        for rate in [8_000, 16_000, 44_100, 48_000, 96_000] {
            let length = rate + 137;
            let output = tone(rate, 1000.0, length);
            let count = length * OUTPUT_RATE / rate;
            assert_eq!(output.len(), count.div_ceil(FRAME) * FRAME);
            assert!(output[count..].iter().all(|&sample| sample == 0));
            // The sinc interpolation grid has a sub-sample phase offset.
            let error = (-128..=128)
                .map(|shift| {
                    (1000..15000)
                        .map(|i| {
                            let time = i as f32 + shift as f32 / 128.0;
                            let expected = 16384.0
                                * (std::f32::consts::TAU * 1000.0 * time / OUTPUT_RATE as f32)
                                    .sin();
                            (output[i] as f32 - expected).abs()
                        })
                        .sum::<f32>()
                        / 14000.0
                })
                .fold(f32::INFINITY, f32::min);
            assert!(error < 20.0, "rate={rate}, mean error={error}");
        }
        assert!(tone(48_000, 1000.0, 0).is_empty());
        assert_eq!(tone(48_000, 1000.0, 5).len(), FRAME);
    }

    #[test]
    fn resampling_keeps_an_impulse_at_its_source_time() {
        for rate in [8_000, 16_000, 44_100, 48_000, 96_000] {
            let position = rate / 3;
            let mut converter = Converter::new(rate).unwrap();
            let mut output = Vec::new();
            let mut emit = |frame: &[i16; FRAME]| {
                output.extend_from_slice(frame);
                Ok(())
            };
            for index in 0..rate {
                converter
                    .push(if index == position { 0.5 } else { 0.0 }, &mut emit)
                    .unwrap();
            }
            converter.finish(&mut emit).unwrap();
            let peak = output
                .iter()
                .enumerate()
                .max_by_key(|(_, sample)| sample.unsigned_abs())
                .unwrap()
                .0;
            assert!(
                peak.abs_diff(position * OUTPUT_RATE / rate) <= 1,
                "rate={rate}, peak={peak}"
            );
        }
    }

    #[test]
    fn resampling_rejects_above_nyquist_instead_of_aliasing() {
        let output = tone(48_000, 12_000.0, 48_000);
        let rms = (output[1000..15000]
            .iter()
            .map(|&x| (x as f64).powi(2))
            .sum::<f64>()
            / 14000.0)
            .sqrt();
        assert!(rms < 20.0, "alias RMS: {rms}");
    }
}
