//! Synthetic worker used only by the direct-runtime integration tests.
use serde_json::{json, Value};
use std::io::{BufRead, Write};

fn packet(value: Value, pcm: &[u8]) {
    let bytes = serde_json::to_vec(&value).unwrap();
    let mut out = std::io::stdout().lock();
    out.write_all(&(bytes.len() as u32).to_le_bytes()).unwrap();
    out.write_all(&(pcm.len() as u32).to_le_bytes()).unwrap();
    out.write_all(&bytes).unwrap();
    out.write_all(pcm).unwrap();
    out.flush().unwrap();
}
fn main() {
    if std::env::current_exe()
        .ok()
        .and_then(|p| p.file_stem().map(|s| s.to_string_lossy().into_owned()))
        .is_some_and(|s| matches!(s.as_str(), "python" | "python3" | "uv"))
    {
        if let Some(marker) = std::env::var_os("MEETING_E2E_PYTHON_MARKER") {
            std::fs::write(marker, "invoked").unwrap();
        }
        std::process::exit(97);
    }
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).is_some_and(|s| s == "--capabilities") {
        let executable = std::env::current_exe().unwrap();
        let name = executable.file_stem().unwrap().to_str().unwrap();
        if name == "hanging-worker" {
            std::thread::sleep(std::time::Duration::from_secs(180));
        }
        println!(
            "{}",
            match name {
                "gpu-worker" => json!({"protocol":1,"whisper_gpu":true}),
                "invalid-worker" => json!({"protocol":1,"whisper_gpu":"false"}),
                "old-worker" => json!({"protocol":2,"whisper_gpu":true}),
                _ => json!({"protocol":1,"whisper_gpu":false}),
            }
        );
        if name == "failed-worker" {
            std::process::exit(1);
        }
        return;
    }
    if args.get(1).is_some_and(|s| s == "convert-document") {
        // Protocol fixture, not a second DOCX implementation.
        let request: Value = serde_json::from_reader(std::io::stdin().lock()).unwrap();
        let results: Vec<Value> = request["files"]
            .as_array()
            .unwrap()
            .iter()
            .map(|file| {
                let path = std::path::Path::new(file["input_path"].as_str().unwrap());
                assert!(path.is_file());
                json!({"id":file["id"],"status":"converted","markdown":"synthetic docx material"})
            })
            .collect();
        println!(
            "{}",
            json!({"protocol":1,"id":request["id"],"results":results})
        );
        return;
    }
    if args.iter().any(|s| s == "--list-devices") {
        packet(
            json!({"type":"devices","devices":[{"index":"mic","name":"Synthetic mic","is_monitor":false},{"index":"monitor","name":"Synthetic monitor","is_monitor":true}]}),
            &[],
        );
        return;
    }
    if args
        .iter()
        .any(|s| s == "--reazon-model" || s == "--whisper-model")
    {
        speech(args);
    } else {
        capture();
    }
}
fn speech(args: Vec<String>) {
    let whisper = args.iter().any(|s| s == "--whisper-model");
    let shared = args.iter().any(|s| s == "--shared");
    let model = std::path::Path::new(
        &args[args
            .iter()
            .position(|s| s == "--reazon-model" || s == "--whisper-model")
            .unwrap()
            + 1],
    );
    let model = if whisper {
        model.parent().unwrap()
    } else {
        model
    };
    std::fs::write(
        model.join(format!("args-{}.json", std::process::id())),
        serde_json::to_vec(&args).unwrap(),
    )
    .unwrap();
    std::fs::write(model.join(format!("pid-{}", std::process::id())), "").unwrap();
    println!(
        "{}",
        json!({"type":"ready","id":null,"protocol":if shared {3} else {2},"transcription_available":true})
    );
    let mut generations = [0_u64; 2];
    let mut sample_counts = [0_usize; 2];
    let (inference, pending) = std::sync::mpsc::channel::<(Value, bool)>();
    let inference_thread = std::thread::spawn(move || {
        for (mut response, slow) in pending {
            if slow {
                std::thread::sleep(std::time::Duration::from_secs(2));
            }
            let segment = response["segment"].take();
            if !segment.is_null() {
                println!(
                    "{}",
                    json!({"id":null,"type":"segment","role":response["role"],"segment":segment})
                );
            }
            response.as_object_mut().unwrap().remove("role");
            println!("{response}");
        }
    });
    for line in std::io::stdin().lock().lines() {
        let value: Value = serde_json::from_str(&line.unwrap()).unwrap();
        let command = &value["command"];
        let role = if command["role"] == "other" { 1 } else { 0 };
        let generation = &mut generations[role];
        let samples = &mut sample_counts[role];
        let mut response = json!({"id":value["id"]});
        match command["op"].as_str().unwrap() {
            "prepare" => {
                std::fs::write(model.join("preparing"), "").unwrap();
                if model.ends_with("hang") {
                    std::thread::sleep(std::time::Duration::from_secs(180));
                }
                response["type"] = json!("prepared");
                if whisper {
                    response["execution_device"] = json!("cpu");
                }
            }
            "configure" => {
                std::fs::write(
                    model.join(format!("configured-{}.json", std::process::id())),
                    command.to_string(),
                )
                .unwrap();
                response["type"] = json!("configured");
            }
            "reset" => {
                *generation += 1;
                *samples = 0;
                response["type"] = json!("reset");
            }
            "audio" => {
                if model.ends_with("crash") {
                    std::process::exit(23);
                }
                *samples += command["pcm"].as_array().unwrap().len();
                response["type"] = json!("audio");
                response["segment"] = Value::Null;
            }
            "finish" => {
                if model.ends_with("slow-finish") {
                    std::fs::write(
                        model.join(format!("finished-samples-{}-{role}", std::process::id())),
                        samples.to_string(),
                    )
                    .unwrap();
                    if !shared {
                        std::thread::sleep(std::time::Duration::from_secs(2));
                    }
                }
                response["type"] = json!("finished");
                response["segment"] = if *samples == 0 {
                    Value::Null
                } else {
                    json!({"generation":generation,"start_sample":0,"end_sample":samples,"recognition":{"status":"recognized","text":format!("synthetic {role}"),"punctuation":{"status":"applied","text":format!("synthetic {role}。")}}})
                };
                if shared {
                    response["role"] = command["role"].clone();
                    inference
                        .send((response, model.ends_with("slow-finish")))
                        .unwrap();
                    continue;
                }
            }
            _ => std::process::exit(2),
        }
        println!("{response}");
    }
    drop(inference);
    inference_thread.join().unwrap();
}
fn capture() {
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        for line in std::io::stdin().lock().lines() {
            if tx
                .send(serde_json::from_str::<Value>(&line.unwrap()).unwrap())
                .is_err()
            {
                break;
            }
        }
    });
    let mut writer: Option<hound::WavWriter<std::io::BufWriter<std::fs::File>>> = None;
    let mut path = std::path::PathBuf::new();
    let mut samples = 0_u64;
    let mut started = 0_u64;
    let mut sequence = 0;
    packet(
        json!({"type":"ready","protocol":1,"rate":16000,"name":"Synthetic"}),
        &[],
    );
    loop {
        match rx.recv_timeout(std::time::Duration::from_millis(20)) {
            Ok(value) => match value["command"]["op"].as_str().unwrap() {
                "start_recording" => {
                    path = value["command"]["path"].as_str().unwrap().into();
                    writer = Some(
                        hound::WavWriter::create(
                            &path,
                            hound::WavSpec {
                                channels: 1,
                                sample_rate: 16000,
                                bits_per_sample: 16,
                                sample_format: hound::SampleFormat::Int,
                            },
                        )
                        .unwrap(),
                    );
                    started = now();
                    samples = 0;
                    packet(json!({"type":"recording_started","id":value["id"]}), &[]);
                }
                "stop_recording" => {
                    let recording = writer.take().map(|w| { w.finalize().unwrap(); json!({"size_bytes":path.metadata().unwrap().len(),"samples":samples,"started_ms":started,"ended_ms":now()}) });
                    if std::env::current_exe().unwrap().file_stem().unwrap()
                        == "recording-failure-worker"
                        && path.file_name().is_some_and(|name| name == "other.wav")
                    {
                        packet(json!({"type":"error","id":value["id"]}), &[]);
                        continue;
                    }
                    packet(
                        json!({"type":"recording_stopped","id":value["id"],"recording":recording}),
                        &[],
                    );
                }
                "shutdown" => {
                    if let Some(w) = writer.take() {
                        w.finalize().unwrap();
                    }
                    packet(json!({"type":"stopped","id":value["id"]}), &[]);
                    break;
                }
                _ => std::process::exit(2),
            },
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                if let Some(w) = &mut writer {
                    for _ in 0..480 {
                        w.write_sample(4096_i16).unwrap();
                    }
                    samples += 480;
                }
                packet(
                    json!({"type":"audio","sequence":sequence,"peak":0.125}),
                    &vec![0; 960],
                );
                sequence += 1;
            }
            Err(_) => break,
        }
    }
}
fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64
}
