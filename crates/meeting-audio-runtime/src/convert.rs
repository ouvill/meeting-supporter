//! Anti-aliased conversion to the worker wire format, outside the CPAL callback.
use crate::Error;
use rubato::{
    Resampler, SincFixedIn, SincInterpolationParameters, SincInterpolationType, WindowFunction,
};
pub const OUTPUT_RATE: usize = 16_000;
pub const FRAME: usize = 480;

/// Stateful anti-aliased conversion. SincFixedIn withholds look-ahead at the end
/// of a block rather than emitting leading silence. Do not skip output_delay()
/// samples: doing so would discard real input. Finish supplies the look-ahead.
pub struct Converter {
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
    pub fn new(rate: usize) -> Result<Self, Error> {
        if !(8_000..=192_000).contains(&rate) {
            return Err(Error::Capture);
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
        .map_err(|_| Error::Capture)?;
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

    pub fn push(
        &mut self,
        sample: f32,
        emit: &mut impl FnMut(&[i16; FRAME]) -> Result<(), Error>,
    ) -> Result<(), Error> {
        if !sample.is_finite() {
            return Err(Error::Capture);
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
        emit: &mut impl FnMut(&[i16; FRAME]) -> Result<(), Error>,
    ) -> Result<(), Error> {
        let (_, written) = self
            .resampler
            .process_into_buffer(&[self.input.as_slice()], &mut self.output, None)
            .map_err(|_| Error::Capture)?;
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

    #[cfg(test)]
    pub fn finish(
        mut self,
        emit: &mut impl FnMut(&[i16; FRAME]) -> Result<(), Error>,
    ) -> Result<(), Error> {
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
