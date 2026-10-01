//! CPAL owns device I/O; mixing is bounded and resampling stays off its callback.
use crate::{convert::Converter, Device, Error};
use cpal::{
    traits::{DeviceTrait, HostTrait, StreamTrait},
    Sample, SampleFormat, SizedSample,
};
use ringbuf::{traits::*, HeapProd, HeapRb};
use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::SyncSender,
        Arc,
    },
    thread,
    time::Duration,
};

fn host() -> Result<cpal::Host, Error> {
    // Explicitly select the sound server on Linux: ALSA does not expose its
    // monitor sources and may bypass the user's desktop audio routing.
    #[cfg(target_os = "linux")]
    let id = cpal::HostId::PulseAudio;
    #[cfg(target_os = "windows")]
    let id = cpal::HostId::Wasapi;
    #[cfg(any(target_os = "linux", target_os = "windows"))]
    return cpal::host_from_id(id).map_err(|_| Error::Device);
    #[cfg(not(any(target_os = "linux", target_os = "windows")))]
    Err(Error::Unsupported)
}

pub fn devices() -> Result<Vec<Device>, Error> {
    // CPAL's public metadata omits monitor_of_sink. Keep Pulse metadata and
    // existing source IDs, while all audio streams are created through CPAL.
    #[cfg(target_os = "linux")]
    return crate::pulse::devices();

    #[cfg(not(target_os = "linux"))]
    {
        let host = host()?;
        let input = host.default_input_device().and_then(|d| d.id().ok());
        let output = host.default_output_device().and_then(|d| d.id().ok());
        let mut result = Vec::new();
        for device in host.devices().map_err(|_| Error::Device)? {
            let is_monitor = device.supports_output();
            if !is_monitor && !device.supports_input() {
                continue;
            }
            let id = device.id().map_err(|_| Error::Device)?;
            let default = if is_monitor { &output } else { &input };
            result.push(Device {
                index: id.to_string(),
                name: device
                    .description()
                    .map_err(|_| Error::Device)?
                    .name()
                    .to_owned(),
                is_monitor,
                is_default: default.as_ref() == Some(&id),
                hostapi: host.id().name(),
                capture: "rust",
            });
        }
        Ok(result)
    }
}

fn selected(host: &cpal::Host, id: &str) -> Result<cpal::Device, Error> {
    #[cfg(target_os = "linux")]
    let id = cpal::DeviceId::new(cpal::HostId::PulseAudio, id);
    #[cfg(not(target_os = "linux"))]
    let id = id.parse().map_err(|_| Error::Device)?;
    host.device_by_id(&id).ok_or(Error::Device)
}

pub enum CaptureEvent {
    Started,
    Frame(Box<[u8; 960]>),
}

pub struct CaptureGuard(Arc<AtomicBool>);
impl Drop for CaptureGuard {
    fn drop(&mut self) {
        // A blocked native call is still bounded by the parent's process timeout.
        self.0.store(true, Ordering::Release);
    }
}

pub fn start(
    id: String,
    sender: SyncSender<CaptureEvent>,
    failed: Arc<AtomicBool>,
) -> CaptureGuard {
    let stop = Arc::new(AtomicBool::new(false));
    let stopped = Arc::clone(&stop);
    thread::spawn(move || {
        if capture(&id, &sender, &failed, &stopped).is_err() {
            failed.store(true, Ordering::Release);
        }
    });
    CaptureGuard(stop)
}

fn capture(
    id: &str,
    sender: &SyncSender<CaptureEvent>,
    failed: &Arc<AtomicBool>,
    stopped: &AtomicBool,
) -> Result<(), Error> {
    let host = host()?;
    let device = selected(&host, id)?;
    // WASAPI output endpoints become loopback input streams. Pulse monitors
    // are already capture devices, so they use their input configuration.
    let loopback = !device.supports_input();
    let supported = if loopback {
        device.default_output_config()
    } else {
        device.default_input_config()
    }
    .map_err(|_| Error::Device)?;
    let rate = supported.sample_rate() as usize;
    let mut converter = Converter::new(rate)?;
    let (producer, mut consumer) = HeapRb::<f32>::new(rate * 5).split();
    let mut config: cpal::StreamConfig = supported.clone().into();
    if cfg!(target_os = "linux") {
        // Pulse's default recording fragments can add seconds of latency.
        config.buffer_size = cpal::BufferSize::Fixed(supported.sample_rate() / 20);
    }
    let (stream, keepalive) = match supported.sample_format() {
        SampleFormat::I8 => streams::<i8>(&device, config, producer, failed, loopback),
        SampleFormat::I16 => streams::<i16>(&device, config, producer, failed, loopback),
        SampleFormat::I24 => streams::<cpal::I24>(&device, config, producer, failed, loopback),
        SampleFormat::I32 => streams::<i32>(&device, config, producer, failed, loopback),
        SampleFormat::I64 => streams::<i64>(&device, config, producer, failed, loopback),
        SampleFormat::U8 => streams::<u8>(&device, config, producer, failed, loopback),
        SampleFormat::U16 => streams::<u16>(&device, config, producer, failed, loopback),
        SampleFormat::U24 => streams::<cpal::U24>(&device, config, producer, failed, loopback),
        SampleFormat::U32 => streams::<u32>(&device, config, producer, failed, loopback),
        SampleFormat::U64 => streams::<u64>(&device, config, producer, failed, loopback),
        SampleFormat::F32 => streams::<f32>(&device, config, producer, failed, loopback),
        SampleFormat::F64 => streams::<f64>(&device, config, producer, failed, loopback),
        _ => Err(Error::Capture),
    }?;
    if let Some(keepalive) = &keepalive {
        keepalive.play().map_err(|_| Error::Capture)?;
    }
    stream.play().map_err(|_| Error::Capture)?;
    sender
        .try_send(CaptureEvent::Started)
        .map_err(|_| Error::Transport)?;
    while !stopped.load(Ordering::Acquire) {
        if failed.load(Ordering::Acquire) {
            return Err(Error::Capture);
        }
        if let Some(sample) = consumer.try_pop() {
            converter.push(sample, &mut |frame| {
                let mut pcm = Box::new([0; 960]);
                for (sample, bytes) in frame.iter().zip(pcm.chunks_exact_mut(2)) {
                    bytes.copy_from_slice(&sample.to_le_bytes());
                }
                // Never silently lose audio in an otherwise successful WAV.
                sender
                    .try_send(CaptureEvent::Frame(pcm))
                    .map_err(|_| Error::Capture)
            })?;
        } else {
            thread::sleep(Duration::from_millis(2));
        }
    }
    // Drop both native streams before the worker thread exits.
    drop(stream);
    drop(keepalive);
    Ok(())
}

fn streams<T>(
    device: &cpal::Device,
    config: cpal::StreamConfig,
    mut producer: HeapProd<f32>,
    failed: &Arc<AtomicBool>,
    loopback: bool,
) -> Result<(cpal::Stream, Option<cpal::Stream>), Error>
where
    T: SizedSample,
    f32: cpal::FromSample<T>,
{
    let data_failed = Arc::clone(failed);
    let error_failed = Arc::clone(failed);
    let channels = usize::from(config.channels);
    let input = device
        .build_input_stream(
            config,
            move |data: &[T], _| enqueue(data, channels, &mut producer, &data_failed),
            move |_| {
                error_failed.store(true, Ordering::Release);
            },
            Some(Duration::from_secs(3)),
        )
        .map_err(|_| Error::Device)?;
    // WASAPI loopback need not deliver callbacks while nothing is playing.
    // A shared-mode silent render stream keeps recording time continuous and
    // lets the ordinary no-progress watchdog continue to detect device faults.
    let keepalive = if cfg!(target_os = "windows") && loopback {
        let error_failed = Arc::clone(failed);
        Some(
            device
                .build_output_stream(
                    config,
                    |data: &mut [T], _| data.fill(T::EQUILIBRIUM),
                    move |_| {
                        error_failed.store(true, Ordering::Release);
                    },
                    Some(Duration::from_secs(3)),
                )
                .map_err(|_| Error::Device)?,
        )
    } else {
        None
    };
    Ok((input, keepalive))
}

// No allocation, blocking, logging, file writes, or resampling on this callback.
fn enqueue<T>(data: &[T], channels: usize, producer: &mut HeapProd<f32>, failed: &AtomicBool)
where
    T: Sample,
    f32: cpal::FromSample<T>,
{
    if failed.load(Ordering::Acquire) {
        return;
    }
    if channels == 0 || !data.len().is_multiple_of(channels) {
        failed.store(true, Ordering::Release);
        return;
    }
    for frame in data.chunks_exact(channels) {
        let mut mono = 0.0;
        for sample in frame {
            let sample = sample.to_sample::<f32>();
            if !sample.is_finite() {
                failed.store(true, Ordering::Release);
                return;
            }
            mono += sample.clamp(-1.0, 1.0) / channels as f32;
        }
        if producer.try_push(mono).is_err() {
            failed.store(true, Ordering::Release);
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn callback_mixes_normalizes_and_fails_closed_on_overflow() {
        let (mut producer, mut consumer) = HeapRb::<f32>::new(2).split();
        let failed = AtomicBool::new(false);
        enqueue(
            &[i16::MIN, i16::MAX, 16384, 16384],
            2,
            &mut producer,
            &failed,
        );
        assert!(consumer.try_pop().unwrap().abs() < 0.0001);
        assert_eq!(consumer.try_pop(), Some(0.5));
        enqueue(&[0u16, 32768, 65535], 1, &mut producer, &failed);
        assert!(failed.load(Ordering::Acquire));
        assert_eq!(consumer.try_pop(), Some(-1.0));
        assert_eq!(consumer.try_pop(), Some(0.0));
        enqueue(&[0.5f32], 1, &mut producer, &failed);
        assert!(consumer.try_pop().is_none());
    }

    #[test]
    fn malformed_channels_and_nonfinite_samples_are_rejected() {
        for (samples, channels) in [(vec![f32::NAN], 1), (vec![1.0], 2), (vec![], 0)] {
            let (mut producer, mut consumer) = HeapRb::<f32>::new(8).split();
            let failed = AtomicBool::new(false);
            enqueue(&samples, channels, &mut producer, &failed);
            assert!(failed.load(Ordering::Acquire));
            assert!(consumer.try_pop().is_none());
        }
    }
}
