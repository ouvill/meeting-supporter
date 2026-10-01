mod shared;
use meeting_native_backend::{
    error::SpeechError,
    microphone,
    protocol::{Command, ModelStatus, Reply, Request, Response},
    session::{Prepared, SessionConfig, SpeechPlan, SpeechSession},
    wav,
};
use std::{
    io::{self, BufRead, Write},
    path::PathBuf,
    sync::{
        Arc, Mutex,
        mpsc::{self, Receiver, TrySendError},
    },
    thread,
    time::Instant,
};

// A single audio worker owns inference and both speakers' states. Control/health
// stays responsive while loading. The queue and wire input are explicitly bounded.
const MAX_REQUEST_BYTES: usize = 16 * 1024;
type Output = Arc<Mutex<io::Stdout>>;
type Status = Arc<Mutex<ModelStatus>>;

fn respond(output: &Output, id: Option<u64>, reply: Reply) -> io::Result<()> {
    let mut writer = output
        .lock()
        .map_err(|_| io::Error::other("output unavailable"))?;
    serde_json::to_writer(&mut *writer, &Response { id, reply })?;
    writer.write_all(b"\n")?;
    writer.flush()
}

fn worker(
    jobs: Receiver<Request>,
    config: SessionConfig,
    output: Output,
    status: Status,
) -> io::Result<()> {
    let mut core: Option<SpeechSession<Prepared>> = None;
    for request in jobs {
        let reply = if matches!(request.command, Command::Prepare {}) {
            if core.is_some() {
                Reply::Prepared {
                    load_ms: 0.0,
                    execution_device: core.as_ref().and_then(SpeechSession::execution_device),
                }
            } else {
                *status
                    .lock()
                    .map_err(|_| io::Error::other("status unavailable"))? = ModelStatus::Loading;
                let start = Instant::now();
                match SpeechSession::new(config.clone()).prepare() {
                    Ok(loaded) => {
                        core = Some(loaded);
                        *status
                            .lock()
                            .map_err(|_| io::Error::other("status unavailable"))? =
                            ModelStatus::Ready;
                        Reply::Prepared {
                            load_ms: start.elapsed().as_secs_f64() * 1000.0,
                            execution_device: core
                                .as_ref()
                                .and_then(SpeechSession::execution_device),
                        }
                    }
                    Err(code) => {
                        *status
                            .lock()
                            .map_err(|_| io::Error::other("status unavailable"))? =
                            ModelStatus::Failed;
                        Reply::Error { code }
                    }
                }
            }
        } else if let Some(audio) = &mut core {
            match audio.execute(request.command) {
                Ok(reply) => reply,
                Err(code) => {
                    // Bad frame lengths are rejected before state mutation. Inference
                    // failures retire both states; continuing with partial state is unsafe.
                    if code.retires_session() {
                        core = None;
                        *status
                            .lock()
                            .map_err(|_| io::Error::other("status unavailable"))? =
                            ModelStatus::Failed;
                    }
                    Reply::Error { code }
                }
            }
        } else {
            Reply::Error {
                code: SpeechError::ModelNotPrepared,
            }
        };
        respond(&output, Some(request.id), reply)?;
    }
    Ok(())
}

/// Read at most one bounded record; oversized input terminates the transport.
fn read_request(reader: &mut impl BufRead, bytes: &mut Vec<u8>) -> io::Result<bool> {
    bytes.clear();
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            return Ok(!bytes.is_empty());
        }
        let count = available
            .iter()
            .position(|&byte| byte == b'\n')
            .map_or(available.len(), |index| index + 1);
        if bytes.len() + count > MAX_REQUEST_BYTES {
            return Err(io::Error::other("request too large"));
        }
        let complete = available[count - 1] == b'\n';
        bytes.extend_from_slice(&available[..count]);
        reader.consume(count);
        if complete {
            return Ok(true);
        }
    }
}

fn run(config: SessionConfig, shared: bool) -> io::Result<()> {
    let transcription_available = !matches!(config.plan, SpeechPlan::SileroOnly);
    let output = Arc::new(Mutex::new(io::stdout()));
    let status = Arc::new(Mutex::new(ModelStatus::Unloaded));
    let (sender, receiver) = mpsc::sync_channel(8);
    let worker_output = Arc::clone(&output);
    let worker_status = Arc::clone(&status);
    let worker = thread::Builder::new()
        .name("onnx-audio".into())
        .spawn(move || {
            if shared {
                shared::worker(receiver, config, worker_output, worker_status)
            } else {
                worker(receiver, config, worker_output, worker_status)
            }
        })?;
    let result = (|| {
        respond(
            &output,
            None,
            Reply::Ready {
                protocol: if shared { 3 } else { 2 },
                models: ModelStatus::Unloaded,
                transcription_available,
            },
        )?;
        let mut input = io::stdin().lock();
        let mut bytes = Vec::with_capacity(MAX_REQUEST_BYTES);
        let mut shutdown_id = None;
        while read_request(&mut input, &mut bytes)? {
            let request: Request = match serde_json::from_slice(&bytes) {
                Ok(request) => request,
                Err(_) => {
                    respond(
                        &output,
                        None,
                        Reply::Error {
                            code: SpeechError::InvalidRequest,
                        },
                    )?;
                    continue;
                }
            };
            match request.command {
                Command::Shutdown {} => {
                    shutdown_id = Some(request.id);
                    break;
                }
                Command::Health {} => {
                    let models = *status
                        .lock()
                        .map_err(|_| io::Error::other("status unavailable"))?;
                    respond(&output, Some(request.id), Reply::Health { models })?;
                }
                _ => match sender.try_send(request) {
                    Ok(()) => {}
                    Err(TrySendError::Full(request)) => {
                        respond(
                            &output,
                            Some(request.id),
                            Reply::Error {
                                code: SpeechError::Busy,
                            },
                        )?;
                    }
                    Err(TrySendError::Disconnected(_)) => {
                        return Err(io::Error::other("audio worker stopped"));
                    }
                },
            }
        }
        Ok(shutdown_id)
    })();
    // EOF and every error path also join the owned worker; shutdown drains accepted work.
    drop(sender);
    worker
        .join()
        .map_err(|_| io::Error::other("audio worker failed"))??;
    if let Some(id) = result? {
        respond(&output, Some(id), Reply::Stopped)?;
    }
    Ok(())
}

enum Input {
    JsonLines {
        shared: bool,
    },
    Wav(PathBuf),
    Microphone {
        device: Option<usize>,
        seconds: Option<u32>,
        desktop: bool,
    },
}

enum Options {
    Capabilities,
    ListDevices,
    Start { config: SessionConfig, input: Input },
}

fn options(
    arguments: impl IntoIterator<Item = std::ffi::OsString>,
) -> Result<Options, &'static str> {
    let mut arguments = arguments.into_iter().peekable();
    if arguments.peek().is_some_and(|arg| arg == "--capabilities") {
        arguments.next();
        return if arguments.next().is_none() {
            Ok(Options::Capabilities)
        } else {
            Err("--capabilities must be used alone")
        };
    }
    let mut library = None;
    let mut model = None;
    let mut punctuation = None;
    let mut whisper = None;
    let mut inference_device = None;
    let mut language = None;
    let mut wav = None;
    let mut mic = false;
    let mut shared = false;
    let mut desktop = false;
    let mut list = false;
    let mut device = None;
    let mut seconds = None;
    while let Some(option) = arguments.next() {
        match option.to_str() {
            Some("--shared") if !shared => {
                shared = true;
                continue;
            }
            Some("--mic") if !mic => {
                mic = true;
                continue;
            }
            Some("--desktop") if !desktop => {
                desktop = true;
                continue;
            }
            Some("--list-input-devices") if !list => {
                list = true;
                continue;
            }
            Some("--input-device") if device.is_none() => {
                device = Some(
                    arguments
                        .next()
                        .and_then(|v| v.to_str().and_then(|s| s.parse::<usize>().ok()))
                        .ok_or("invalid --input-device index")?,
                );
                continue;
            }
            Some("--seconds") if seconds.is_none() => {
                seconds = Some(
                    arguments
                        .next()
                        .and_then(|v| v.to_str().and_then(|s| s.parse::<u32>().ok()))
                        .filter(|&v| v > 0)
                        .ok_or("--seconds must be a positive integer")?,
                );
                continue;
            }
            _ => {}
        }
        let slot = match option.to_str() {
            Some("--ort-library") => &mut library,
            Some("--reazon-model") => &mut model,
            Some("--whisper-model") => &mut whisper,
            Some("--inference-device") => &mut inference_device,
            Some("--language") => &mut language,
            Some("--punctuation-model") => &mut punctuation,
            Some("--wav") => &mut wav,
            _ => return Err("unknown or duplicate option"),
        };
        let value = arguments.next().ok_or("missing argument")?;
        if slot.replace(PathBuf::from(value)).is_some() {
            return Err("duplicate option");
        }
    }
    if list {
        if shared
            || mic
            || desktop
            || wav.is_some()
            || device.is_some()
            || seconds.is_some()
            || punctuation.is_some()
            || model.is_some()
            || library.is_some()
            || whisper.is_some()
            || inference_device.is_some()
            || language.is_some()
        {
            return Err("--list-input-devices must be used alone");
        }
        return Ok(Options::ListDevices);
    }
    if shared && (mic || wav.is_some()) {
        return Err("--shared requires JSON Lines input");
    }
    if desktop && !mic {
        return Err("--desktop requires --mic");
    }
    if !mic && (device.is_some() || seconds.is_some()) {
        return Err("--input-device and --seconds require --mic");
    }
    if mic && wav.is_some() {
        return Err("--mic and --wav are mutually exclusive");
    }
    if whisper.is_some() && !cfg!(feature = "whisper") {
        return Err("build with --features whisper");
    }
    if whisper.is_some() && (model.is_some() || punctuation.is_some()) {
        return Err("Whisper cannot be combined with ReazonSpeech or punctuation restoration");
    }
    if whisper.is_none() && (inference_device.is_some() || language.is_some()) {
        return Err("--inference-device and --language require --whisper-model");
    }
    let inference_device = match inference_device
        .as_deref()
        .and_then(|v| v.to_str())
        .unwrap_or("auto")
    {
        "auto" => meeting_native_backend::whisper::Device::Auto,
        "cpu" => meeting_native_backend::whisper::Device::Cpu,
        "gpu" => meeting_native_backend::whisper::Device::Gpu,
        _ => return Err("invalid inference device"),
    };
    let language = match language.as_deref().and_then(|v| v.to_str()).unwrap_or("ja") {
        "ja" => meeting_native_backend::whisper::Language::Ja,
        "en" => meeting_native_backend::whisper::Language::En,
        "auto" => meeting_native_backend::whisper::Language::Auto,
        _ => return Err("invalid language"),
    };
    if model.is_some() && !cfg!(feature = "reazonspeech") {
        return Err("build with --features reazonspeech");
    }
    if (wav.is_some() || mic) && model.is_none() && whisper.is_none() {
        return Err("--wav and --mic require --reazon-model");
    }
    if punctuation.is_some() && model.is_none() {
        return Err("--punctuation-model requires --reazon-model");
    }
    let input = if mic {
        Input::Microphone {
            device,
            seconds,
            desktop,
        }
    } else if let Some(path) = wav {
        Input::Wav(path)
    } else {
        Input::JsonLines { shared }
    };
    // The full speech worker shares the runtime linked by sherpa-onnx. Do not
    // load an arbitrary second ORT alongside it. Libraries are packaged adjacent.
    #[cfg(feature = "reazonspeech")]
    let library = {
        if library.is_some() {
            return Err("ReazonSpeech builds use the bundled runtime; omit --ort-library");
        }
        let executable = std::env::current_exe().map_err(|_| "executable unavailable")?;
        let directory = executable
            .parent()
            .ok_or("executable directory unavailable")?;
        let name = if cfg!(target_os = "windows") {
            "onnxruntime.dll"
        } else if cfg!(target_os = "macos") {
            "libonnxruntime.dylib"
        } else {
            "libonnxruntime.so"
        };
        directory.join(name)
    };
    #[cfg(not(feature = "reazonspeech"))]
    let library = library.ok_or("--ort-library is required")?;
    Ok(Options::Start {
        config: SessionConfig {
            runtime_library: library,
            plan: if let Some(model) = whisper {
                SpeechPlan::SileroWhisper {
                    model,
                    device: inference_device,
                    language,
                }
            } else {
                match model {
                    Some(model_directory) => SpeechPlan::SileroReazon {
                        model_directory,
                        punctuation_directory: punctuation,
                    },
                    None => SpeechPlan::SileroOnly,
                }
            },
        },
        input,
    })
}

fn main() {
    let options = match options(std::env::args_os().skip(1)) {
        Ok(options) => options,
        Err(message) => {
            eprintln!("{message}");
            eprintln!(
                "usage: meeting-native-backend [--ort-library <library>] [--reazon-model <directory> [--punctuation-model <directory>] | --whisper-model <ggml.bin> [--inference-device auto|cpu|gpu] [--language ja|en|auto]] [--shared | --wav <16 kHz mono PCM16 WAV> | --mic [--desktop] [--input-device <index>] [--seconds <n>]] | --list-input-devices | --capabilities"
            );
            std::process::exit(2);
        }
    };
    let result = match options {
        Options::Capabilities => {
            // Report build support without opening models, audio devices or GPU drivers.
            let capabilities = serde_json::json!({
                "protocol": 1,
                "whisper_gpu": meeting_native_backend::whisper::gpu_supported(),
            });
            let mut output = io::stdout().lock();
            serde_json::to_writer(&mut output, &capabilities)
                .map_err(io::Error::other)
                .and_then(|()| output.write_all(b"\n"))
        }
        Options::ListDevices => microphone::list_devices().map_err(io::Error::other),
        Options::Start { config, input } => match input {
            Input::Wav(path) => wav::transcribe(config, &path),
            Input::Microphone {
                device,
                seconds,
                desktop,
            } => microphone::transcribe(config, device, seconds, desktop).map_err(io::Error::other),
            Input::JsonLines { shared } => run(config, shared),
        },
    };
    if let Err(error) = result {
        // Microphone errors contain public codes and guidance, never native diagnostics.
        if let Some(error) = error
            .get_ref()
            .and_then(|e| e.downcast_ref::<microphone::MicrophoneError>())
        {
            eprintln!("{error}");
        } else {
            eprintln!("native backend stopped: input, model, transport or worker failure");
        }
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn microphone_options_reject_ambiguous_or_incomplete_input() {
        for args in [
            vec!["--mic", "--wav", "synthetic.wav"],
            vec!["--input-device", "0"],
            vec!["--mic", "--input-device", "-1"],
            vec!["--mic", "--seconds", "0"],
            vec!["--list-input-devices", "--mic"],
            vec!["--mic", "--mic"],
            vec!["--shared", "--shared"],
            vec!["--shared", "--mic"],
            vec!["--shared", "--wav", "synthetic.wav"],
            vec!["--mic"],
            vec!["--whisper-model", "model", "--reazon-model", "model"],
            vec!["--whisper-model", "model", "--punctuation-model", "model"],
            vec!["--whisper-model", "model", "--language", "invalid"],
            vec!["--whisper-model", "model", "--inference-device", "cuda"],
            vec!["--language", "ja"],
            vec!["--desktop"],
            vec!["--punctuation-model", "model"],
            vec!["--list-input-devices", "--punctuation-model", "model"],
        ] {
            assert!(options(args.into_iter().map(Into::into)).is_err());
        }
        assert!(matches!(
            options(["--list-input-devices".into()]),
            Ok(Options::ListDevices)
        ));
    }

    #[test]
    fn input_is_bounded_and_handles_eof() {
        let mut bytes = Vec::new();
        assert!(!read_request(&mut io::Cursor::new(b""), &mut bytes).unwrap());
        assert!(read_request(&mut io::Cursor::new(b"{}"), &mut bytes).unwrap());
        assert_eq!(bytes, b"{}");
        let oversized = vec![b'x'; MAX_REQUEST_BYTES + 1];
        assert!(read_request(&mut io::Cursor::new(oversized), &mut bytes).is_err());
        assert!(bytes.len() <= MAX_REQUEST_BYTES);
    }

    #[test]
    fn protocol_rejects_unknown_fields_and_out_of_range_pcm() {
        for input in [
            r#"{"id":1,"command":{"op":"health","extra":true}}"#,
            r#"{"id":1,"command":{"op":"audio","role":"user","pcm":[32768]}}"#,
            r#"{"id":1,"command":{"op":"audio","role":"unknown","pcm":[]}}"#,
        ] {
            assert!(serde_json::from_str::<Request>(input).is_err());
        }
    }
}
