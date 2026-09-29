//! Tauri adapter; the runtime owns process lifetime and session state.
use meeting_speech_runtime::{Config, Error, Runtime, Snapshot};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use tauri::{AppHandle, Manager, State};

#[derive(Default)]
pub struct NativeSpeechState(pub Runtime);

#[derive(Serialize)]
pub struct Defaults {
    model_directory: PathBuf,
    punctuation_directory: Option<PathBuf>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StartOptions {
    model_directory: PathBuf,
    punctuation_directory: Option<PathBuf>,
    device: Option<usize>,
}

#[tauri::command]
pub fn get_runtime_mode() -> &'static str {
    if cfg!(feature = "rust-backend") {
        "rust-backend"
    } else if cfg!(feature = "native-speech") {
        "rust"
    } else {
        "python"
    }
}

fn model_root(app: &AppHandle) -> Result<PathBuf, Error> {
    if cfg!(debug_assertions) {
        Ok(PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../test/rust-native-backend/target/models"))
    } else {
        app.path()
            .app_data_dir()
            .map(|p| p.join("models"))
            .map_err(|_| Error::ModelUnavailable)
    }
}

fn worker(app: &AppHandle) -> Result<PathBuf, Error> {
    let name = if cfg!(windows) {
        "meeting-native-backend.exe"
    } else {
        "meeting-native-backend"
    };
    if cfg!(debug_assertions) {
        if let Some(path) = std::env::var_os("MEETING_NATIVE_WORKER") {
            return Ok(path.into());
        }
        Ok(PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../test/rust-native-backend/target/release")
            .join(name))
    } else {
        app.path()
            .resource_dir()
            .map(|p| p.join("native").join(name))
            .map_err(|_| Error::WorkerUnavailable)
    }
}

#[tauri::command]
pub fn native_speech_defaults(app: AppHandle) -> Result<Defaults, Error> {
    let root = model_root(&app)?;
    let punctuation = root.join("punctuation-bert");
    Ok(Defaults {
        model_directory: root.join("reazonspeech"),
        punctuation_directory: punctuation.is_dir().then_some(punctuation),
    })
}

#[tauri::command]
pub fn native_speech_snapshot(state: State<NativeSpeechState>) -> Snapshot {
    state.0.snapshot()
}

#[tauri::command]
pub fn native_speech_start(
    app: AppHandle,
    state: State<NativeSpeechState>,
    options: StartOptions,
) -> Result<Snapshot, Error> {
    if !cfg!(feature = "native-speech") {
        return Err(Error::Busy);
    }
    state.0.start(Config {
        worker: worker(&app)?,
        model: options.model_directory,
        punctuation: options.punctuation_directory,
        device: options.device,
    })
}

#[tauri::command]
pub fn native_speech_stop(state: State<NativeSpeechState>) -> Snapshot {
    state.0.stop()
}

#[tauri::command]
pub fn native_speech_clear(state: State<NativeSpeechState>) -> Result<Snapshot, Error> {
    state.0.clear()
}

pub fn shutdown(app: &AppHandle) {
    app.state::<NativeSpeechState>().0.shutdown();
}

#[tauri::command]
pub fn native_speech_export(
    state: State<NativeSpeechState>,
    path: PathBuf,
) -> Result<(), &'static str> {
    use std::io::Write;
    let snapshot = state.0.snapshot();
    let parent = path.parent().ok_or("export_failed")?;
    let temporary = parent.join(format!(
        ".meeting-transcript-{:016x}.tmp",
        rand::random::<u64>()
    ));
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary).map_err(|_| "export_failed")?;
    let result = (|| -> std::io::Result<()> {
        for transcript in snapshot.transcripts {
            writeln!(file, "{}", transcript.text)?;
        }
        file.sync_all()?;
        drop(file);
        std::fs::rename(&temporary, path)
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result.map_err(|_| "export_failed")
}
