use clap::Parser;
use meeting_whisper_probe::{Audio, Device, Language, Transcriber};
use std::{
    io::{self, Write},
    path::PathBuf,
};

#[derive(Parser)]
#[command(
    about = "Local whisper.cpp WAV probe (GGML FP16 / Q8_0). No network or microphone access."
)]
struct Args {
    #[arg(long)]
    model: PathBuf,
    /// Mono 16 kHz PCM16, 0–30 seconds.
    #[arg(long)]
    wav: PathBuf,
    #[arg(long, value_enum, default_value = "ja")]
    language: Language,
    #[arg(long, value_enum, default_value = "cpu")]
    device: Device,
    #[arg(long, default_value_t=4, value_parser=clap::value_parser!(i32).range(1..=64))]
    threads: i32,
    /// Reuse model weights; each run starts with fresh decoding state.
    #[arg(long, default_value_t=1, value_parser=clap::value_parser!(u32).range(1..=20))]
    runs: u32,
}
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let audio = Audio::read(&args.wav)?;
    let (model, prepared) = Transcriber::new(args.model, args.device).prepare()?;
    let mut output = io::stdout().lock();
    serde_json::to_writer(
        &mut output,
        &serde_json::json!({"type":"prepared","details":prepared}),
    )?;
    writeln!(output)?;
    output.flush()?;
    for run in 1..=args.runs {
        let transcript = model.transcribe(&audio, args.language, args.threads)?;
        serde_json::to_writer(
            &mut output,
            &serde_json::json!({"type":"transcript","run":run,"result":transcript}),
        )?;
        writeln!(output)?;
        output.flush()?;
    }
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
