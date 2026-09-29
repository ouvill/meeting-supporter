use crate::Error;
use hound::{SampleFormat, WavSpec, WavWriter};
use serde::Serialize;
use std::{
    fs::{File, OpenOptions},
    io::BufWriter,
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Serialize)]
pub struct RecordingInfo {
    pub size_bytes: u64,
    pub started_ms: u64,
    pub ended_ms: u64,
    pub samples: u64,
}

pub(super) struct Active {
    writer: WavWriter<BufWriter<File>>,
    path: PathBuf,
    started_ms: u64,
    samples: u64,
}

#[derive(Default)]
pub enum Recorder {
    #[default]
    Idle,
    Active(Box<Active>),
    Failed,
}

fn now() -> Result<u64, Error> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .map_err(|_| Error::Recording)
}

impl Recorder {
    pub fn start(&mut self, path: PathBuf) -> Result<(), Error> {
        if !matches!(self, Self::Idle) {
            return Err(Error::RecordingBusy);
        }
        let started_ms = now()?;
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = options.open(&path).map_err(|_| Error::Recording)?;
        let writer = WavWriter::new(
            BufWriter::new(file),
            WavSpec {
                channels: 1,
                sample_rate: 16_000,
                bits_per_sample: 16,
                sample_format: SampleFormat::Int,
            },
        )
        .map_err(|_| Error::Recording)?;
        *self = Self::Active(Box::new(Active {
            writer,
            path,
            started_ms,
            samples: 0,
        }));
        Ok(())
    }

    pub fn write(&mut self, pcm: &[u8; 960]) -> Result<(), Error> {
        if let Self::Active(active) = self {
            for bytes in pcm.as_chunks::<2>().0.iter() {
                if active
                    .writer
                    .write_sample(i16::from_le_bytes([bytes[0], bytes[1]]))
                    .is_err()
                {
                    *self = Self::Failed;
                    return Err(Error::Recording);
                }
            }
            active.samples += 480;
        }
        Ok(())
    }

    pub fn stop(&mut self) -> Result<Option<RecordingInfo>, Error> {
        match std::mem::take(self) {
            Self::Idle => Ok(None),
            Self::Failed => Err(Error::Recording),
            Self::Active(active) => {
                active.writer.finalize().map_err(|_| Error::Recording)?;
                let file = File::open(&active.path).map_err(|_| Error::Recording)?;
                file.sync_all().map_err(|_| Error::Recording)?;
                let size_bytes = file.metadata().map_err(|_| Error::Recording)?.len();
                Ok(Some(RecordingInfo {
                    size_bytes,
                    started_ms: active.started_ms,
                    ended_ms: now()?,
                    samples: active.samples,
                }))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn recording_preserves_pcm_and_refuses_overwrite() {
        let path = std::env::temp_dir().join(format!(
            "meeting-audio-test-{}-{}.wav",
            std::process::id(),
            now().unwrap()
        ));
        let mut recorder = Recorder::default();
        recorder.start(path.clone()).unwrap();
        assert!(recorder.start(path.clone()).is_err());
        let mut pcm = [0u8; 960];
        for (i, pair) in pcm.as_chunks_mut::<2>().0.iter_mut().enumerate() {
            pair.copy_from_slice(&(i as i16 - 240).to_le_bytes());
        }
        recorder.write(&pcm).unwrap();
        recorder.write(&pcm).unwrap();
        let info = recorder.stop().unwrap().unwrap();
        assert_eq!(info.samples, 960);
        let mut reader = hound::WavReader::open(&path).unwrap();
        assert_eq!(reader.spec().sample_rate, 16_000);
        let samples: Vec<i16> = reader.samples().map(Result::unwrap).collect();
        assert_eq!(samples.len(), 960);
        assert_eq!(samples[0], -240);
        assert_eq!(samples[959], 239);
        assert!(recorder.stop().unwrap().is_none());
        assert!(recorder.start(path.clone()).is_err());
        std::fs::remove_file(path).unwrap();
    }
}
