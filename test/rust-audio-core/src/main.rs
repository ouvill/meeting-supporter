//! Bounded binary replay adapter. See README for the experimental wire format.
use meeting_audio_core::{Config, FRAME_BYTES, Segment, Segmenter};
use std::io::{self, Read, Write};
use std::time::Instant;

fn write_segment(out: &mut impl Write, index: u64, segment: Segment) -> io::Result<()> {
    if segment.accepted {
        out.write_all(&index.to_le_bytes())?;
        out.write_all(&(segment.audio.len() as u32).to_le_bytes())?;
        for sample in segment.audio {
            out.write_all(&sample.to_le_bytes())?;
        }
    }
    Ok(())
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let config = match args.as_slice() {
        [] => Config::default(),
        [silence, voiced, ratio, rms] => Config {
            silence_seconds: silence.parse()?,
            min_voiced_ms: voiced.parse()?,
            min_voiced_ratio: ratio.parse()?,
            min_rms_dbfs: rms.parse()?,
        },
        _ => return Err("usage: meeting-audio-core [silence_seconds min_voiced_ms min_voiced_ratio min_rms_dbfs]".into()),
    };
    let mut core = Segmenter::new(config)?;
    let mut input = io::BufReader::new(io::stdin().lock());
    let mut output = io::BufWriter::new(io::stdout().lock());
    let mut frame = [0; FRAME_BYTES];
    let mut index = 0;
    let mut core_time = std::time::Duration::ZERO;
    loop {
        let mut flag = [0];
        if input.read(&mut flag)? == 0 {
            break;
        }
        if flag[0] > 1 {
            return Err("invalid VAD flag".into());
        }
        input.read_exact(&mut frame)?;
        let start = Instant::now();
        let result = core.push(&frame, flag[0] == 1)?;
        core_time += start.elapsed();
        if let Some(segment) = result {
            write_segment(&mut output, index, segment)?;
        }
        index += 1;
    }
    let start = Instant::now();
    let last = core.finish();
    core_time += start.elapsed();
    if let Some(segment) = last {
        write_segment(&mut output, index, segment)?;
    }
    output.flush()?;
    eprintln!("{}", core_time.as_nanos());
    Ok(())
}

fn main() {
    if run().is_err() {
        eprintln!("audio replay failed: check arguments, VAD flag and PCM frame length");
        std::process::exit(1);
    }
}
