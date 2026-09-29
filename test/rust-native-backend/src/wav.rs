//! Bounded-memory WAV input for exercising the complete speech path without Python.
use crate::{
    protocol::{Reply, Role},
    session::{SessionConfig, SpeechSession},
};
use meeting_audio_core::FRAME_SAMPLES;
use std::{
    io::{self, Write},
    path::Path,
};

pub fn transcribe(config: SessionConfig, path: &Path) -> io::Result<()> {
    let mut reader = hound::WavReader::open(path).map_err(io::Error::other)?;
    let spec = reader.spec();
    if spec.channels != 1
        || spec.sample_rate != 16_000
        || spec.bits_per_sample != 16
        || spec.sample_format != hound::SampleFormat::Int
    {
        return Err(io::Error::other("expected 16 kHz mono PCM16 WAV"));
    }
    let mut session = SpeechSession::new(config)
        .prepare()
        .map_err(io::Error::other)?;
    let mut output = io::stdout().lock();
    let mut frame = [0i16; FRAME_SAMPLES];
    let mut count = 0;
    for sample in reader.samples::<i16>() {
        frame[count] = sample.map_err(io::Error::other)?;
        count += 1;
        if count == FRAME_SAMPLES {
            emit_segment(
                &mut output,
                session
                    .audio(Role::User, &frame)
                    .map_err(io::Error::other)?,
            )?;
            count = 0;
        }
    }
    if count > 0 {
        frame[count..].fill(0);
        emit_segment(
            &mut output,
            session
                .audio(Role::User, &frame)
                .map_err(io::Error::other)?,
        )?;
    }
    emit_segment(
        &mut output,
        session.finish(Role::User).map_err(io::Error::other)?,
    )
}

pub(crate) fn emit_segment(output: &mut impl Write, reply: Reply) -> io::Result<()> {
    match reply {
        Reply::Audio {
            segment: Some(segment),
            ..
        }
        | Reply::Finished {
            segment: Some(segment),
        } => {
            serde_json::to_writer(&mut *output, &segment)?;
            output.write_all(b"\n")?;
            output.flush()
        }
        _ => Ok(()),
    }
}
