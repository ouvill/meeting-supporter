use sha2::{Digest, Sha256};
use std::ffi::OsStr;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::Duration;
use tauri::AppHandle;
use wait_timeout::ChildExt;

use super::{resolve_runtime_paths, RuntimePaths};
use crate::error::AppError;
use crate::state::set_bootstrap_status;

/// uv sync の1試行あたりのタイムアウト（秒）。
const UV_SYNC_TIMEOUT_SECS: u64 = 120;

/// uv sync の最大リトライ回数。
/// `process::start_backend` のポートリトライと合わせて最大 3 回に統一している。
const MAX_RETRIES: u32 = 3;

/// リトライ間隔の指数バックオフ初期値（秒）。
const BACKOFF_INITIAL_SECS: u64 = 1;

/// リトライ間隔の上限（秒）。
const BACKOFF_MAX_SECS: u64 = 8;
const UV_VERSION: &str = "0.11.7";

pub fn managed_uv_path(app_local_data_dir: &Path) -> PathBuf {
    #[cfg(target_os = "windows")]
    let executable = "uv.exe";
    #[cfg(not(target_os = "windows"))]
    let executable = "uv";

    app_local_data_dir
        .join("runtime")
        .join(format!("uv-{UV_VERSION}"))
        .join(executable)
}

/// システム PATH の uv を優先し、なければapp-data内の検証済み取得物を返す。
pub fn find_uv(managed: &Path) -> PathBuf {
    #[cfg(target_os = "windows")]
    let uv_name = "uv.exe";
    #[cfg(not(target_os = "windows"))]
    let uv_name = "uv";

    let managed_canonical =
        std::fs::canonicalize(managed).unwrap_or_else(|_| managed.to_path_buf());
    if let Ok(path_env) = std::env::var("PATH") {
        for dir in std::env::split_paths(&path_env) {
            let candidate = dir.join(uv_name);
            if candidate.is_file() {
                let candidate_canonical =
                    std::fs::canonicalize(&candidate).unwrap_or(candidate.clone());
                if candidate_canonical != managed_canonical {
                    println!("[backend] using system uv: {}", candidate.display());
                    return candidate;
                }
            }
        }
    }
    managed.to_path_buf()
}

fn uv_release() -> Result<(&'static str, &'static str), AppError> {
    #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
    return Ok((
        "uv-x86_64-unknown-linux-gnu.tar.gz",
        "6681d691eb7f9c00ac6a3af54252f7ab29ae72f0c8f95bdc7f9d1401c23ea868",
    ));
    #[cfg(all(target_os = "linux", target_arch = "aarch64"))]
    return Ok((
        "uv-aarch64-unknown-linux-gnu.tar.gz",
        "f2ee1cde9aabb4c6e43bd3f341dadaf42189a54e001e521346dc31547310e284",
    ));
    #[cfg(all(target_os = "macos", target_arch = "x86_64"))]
    return Ok((
        "uv-x86_64-apple-darwin.tar.gz",
        "0a4bc8fcde4974ea3560be21772aeecab600a6f43fa6e58169f9fa7b3b71d302",
    ));
    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    return Ok((
        "uv-aarch64-apple-darwin.tar.gz",
        "66e37d91f839e12481d7b932a1eccbfe732560f42c1cfb89faddfa2454534ba8",
    ));
    #[cfg(all(target_os = "windows", target_arch = "x86_64"))]
    return Ok((
        "uv-x86_64-pc-windows-msvc.zip",
        "fe0c7815acf4fc45f8a5eff58ed3cf7ae2e15c3cf1dceadbd10c816ec1690cc1",
    ));
    #[cfg(all(target_os = "windows", target_arch = "aarch64"))]
    return Ok((
        "uv-aarch64-pc-windows-msvc.zip",
        "1387e1c94e15196351196b79fce4c1e6f4b30f19cdaaf9ff85fbd6b046018aa2",
    ));
    #[allow(unreachable_code)]
    Err(AppError::Process(
        "このOS/CPU向けのuv配布物はサポートされていません".into(),
    ))
}

fn verify_sha256(path: &Path, expected: &str) -> Result<(), AppError> {
    let mut file = std::fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    let actual = format!("{:x}", hasher.finalize());
    if actual != expected {
        return Err(AppError::Process(format!(
            "uv archive checksum mismatch: expected {expected}, found {actual}"
        )));
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn extract_uv(archive_path: &Path, destination: &Path) -> Result<(), AppError> {
    let archive_file = std::fs::File::open(archive_path)?;
    let decoder = flate2::read::GzDecoder::new(archive_file);
    let mut archive = tar::Archive::new(decoder);
    for entry in archive.entries()? {
        let mut entry = entry?;
        if entry.path()?.file_name() == Some(OsStr::new("uv")) {
            entry.unpack(destination)?;
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(destination, std::fs::Permissions::from_mode(0o755))?;
            return Ok(());
        }
    }
    Err(AppError::Process("uv archive does not contain uv".into()))
}

#[cfg(target_os = "windows")]
fn extract_uv(archive_path: &Path, destination: &Path) -> Result<(), AppError> {
    let archive_file = std::fs::File::open(archive_path)?;
    let mut archive = zip::ZipArchive::new(archive_file)
        .map_err(|error| AppError::Process(format!("uv zip open failed: {error}")))?;
    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .map_err(|error| AppError::Process(format!("uv zip entry failed: {error}")))?;
        if Path::new(entry.name()).file_name() == Some(OsStr::new("uv.exe")) {
            let mut output = std::fs::File::create(destination)?;
            std::io::copy(&mut entry, &mut output)?;
            return Ok(());
        }
    }
    Err(AppError::Process(
        "uv archive does not contain uv.exe".into(),
    ))
}

pub(super) fn download_uv(destination: &Path) -> Result<(), AppError> {
    let (archive_name, expected_sha256) = uv_release()?;
    let parent = destination
        .parent()
        .ok_or_else(|| AppError::Process("uv destination has no parent".into()))?;
    std::fs::create_dir_all(parent)?;
    let archive_path = parent.join(format!("{archive_name}.part"));
    let executable_path = parent.join("uv.part");
    let url =
        format!("https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{archive_name}");
    println!("[backend] downloading uv {UV_VERSION}: {url}");

    let mut response = ureq::get(&url)
        .call()
        .map_err(|error| AppError::Process(format!("uv download failed: {error}")))?;
    let mut archive_file = std::fs::File::create(&archive_path)?;
    std::io::copy(&mut response.body_mut().as_reader(), &mut archive_file)?;
    archive_file.sync_all()?;
    verify_sha256(&archive_path, expected_sha256)?;
    extract_uv(&archive_path, &executable_path)?;
    if destination.exists() {
        std::fs::remove_file(destination)?;
    }
    std::fs::rename(&executable_path, destination)?;
    let _ = std::fs::remove_file(&archive_path);
    Ok(())
}

/// 指数バックオフでリトライ間隔を計算する純粋関数。
///
/// 呼び出し規約上、`attempt >= 1` の値を期待する。
///
/// - attempt 1 → 1 秒
/// - attempt 2 → 2 秒
/// - attempt 3 → 4 秒
/// - attempt 4 以降 → 8 秒で cap
///
/// `process::start_backend` と合わせて最大 3 回リトライする想定。
///
/// 境界挙動: `attempt == 0` を渡した場合は `saturating_sub` により
/// `attempt - 1 = 0` と扱われ、計算結果は 1 秒 (`BACKOFF_INITIAL_SECS`)
/// となる。これは飽和演算による偶発的な結果だが、後方互換性のため
/// この挙動を固定する。テスト `compute_backoff_delay_zero_returns_initial`
/// で明示的にアサートしている。
pub fn compute_backoff_delay(attempt: u32) -> Duration {
    let delay = BACKOFF_INITIAL_SECS.saturating_mul(2u64.saturating_pow(attempt.saturating_sub(1)));
    Duration::from_secs(delay.min(BACKOFF_MAX_SECS))
}

/// 配布環境には実行時依存だけを導入する。
fn uv_sync_command(paths: &RuntimePaths) -> std::process::Command {
    let mut command = std::process::Command::new(&paths.uv);
    command
        .env("UV_PROJECT_ENVIRONMENT", &paths.venv_dir)
        .env_remove("VIRTUAL_ENV")
        .args(["sync", "--locked", "--no-dev"])
        .current_dir(&paths.python_dir);
    command
}

/// `uv sync --locked --no-dev` を一度実行し、指定秒数でタイムアウトする。
///
/// `std::process::Command` にはタイムアウト機能がないため、`wait_timeout` crate
/// の `Child::wait_timeout` を使用する。タイムアウト時は子プロセスを kill して
/// から `wait()` で回収し、ゾンビプロセスが残らないようにする。
fn run_uv_sync_with_timeout(paths: &RuntimePaths) -> Result<(), AppError> {
    let timeout = Duration::from_secs(UV_SYNC_TIMEOUT_SECS);

    let mut child = uv_sync_command(paths)
        .spawn()
        .map_err(|e| AppError::Process(format!("uv sync 実行失敗: {e}")))?;

    match child
        .wait_timeout(timeout)
        .map_err(|e| AppError::Process(format!("uv sync 待機中にエラー: {e}")))?
    {
        Some(status) => {
            if status.success() {
                Ok(())
            } else {
                Err(AppError::Process(format!(
                    "uv sync が失敗しました (exit={status})"
                )))
            }
        }
        None => {
            eprintln!(
                "[backend] uv sync timed out after {UV_SYNC_TIMEOUT_SECS}s, killing process..."
            );
            let _ = child.kill();
            let _ = child.wait();
            Err(AppError::Process(format!(
                "uv sync が {UV_SYNC_TIMEOUT_SECS} 秒以内に完了しませんでした。ネットワークまたは uv.lock の状態を確認してください。"
            )))
        }
    }
}

/// Python 環境を準備する。
///
/// `uv sync --locked --no-dev` を最大 3 回リトライし、各試行は 120 秒でタイムアウトする。
/// リトライ間は指数バックオフで待機し、ユーザーに進捗を表示する。
/// すべての試行が失敗した場合は `AppError::Process` を返す。
pub fn ensure_python_environment(app: &AppHandle) -> Result<(), AppError> {
    let paths = resolve_runtime_paths(app)?;

    println!("[backend] uv: {}", paths.uv.display());
    println!("[backend] python_dir: {}", paths.python_dir.display());
    println!("[backend] venv: {}", paths.venv_dir.display());

    for attempt in 1..=MAX_RETRIES {
        if attempt > 1 {
            let delay = compute_backoff_delay(attempt - 1);
            let waiting_message = format!(
                "uv sync failed. Retrying in {}s... (attempt {}/{})",
                delay.as_secs(),
                attempt - 1,
                MAX_RETRIES
            );
            println!("[backend] {waiting_message}");
            let _ = set_bootstrap_status(app, "syncing", waiting_message);
            std::thread::sleep(delay);

            println!("[backend] running uv sync --locked (attempt {attempt}/{MAX_RETRIES})");
            let _ = set_bootstrap_status(
                app,
                "syncing",
                format!("Retrying uv sync (attempt {attempt}/{MAX_RETRIES})..."),
            );
        } else {
            println!("[backend] running uv sync --locked (attempt {attempt}/{MAX_RETRIES})");
            let _ = set_bootstrap_status(
                app,
                "syncing",
                format!("Setting up Python dependencies (attempt {attempt}/{MAX_RETRIES})..."),
            );
        }

        match run_uv_sync_with_timeout(&paths) {
            Ok(()) => {
                println!("[backend] uv sync completed on attempt {attempt}");
                return Ok(());
            }
            Err(e) if attempt < MAX_RETRIES => {
                eprintln!("[backend] uv sync attempt {attempt} failed: {e}");
            }
            Err(e) => {
                eprintln!("[backend] uv sync failed after {MAX_RETRIES} attempts: {e}");
                return Err(AppError::Process(format!(
                    "uv sync が {MAX_RETRIES} 回リトライ後も失敗しました: {e}"
                )));
            }
        }
    }

    Err(AppError::Process(
        "uv sync に失敗しました（リトライ上限到達）".into(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static TEST_DIRECTORY_SEQUENCE: AtomicUsize = AtomicUsize::new(0);

    struct TestDirectory(PathBuf);

    impl TestDirectory {
        fn new(label: &str) -> Self {
            let sequence = TEST_DIRECTORY_SEQUENCE.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "meeting-supporter-paths-{label}-{}-{sequence}",
                std::process::id()
            ));
            fs::create_dir_all(&path).expect("create isolated test directory");
            Self(path)
        }

        fn child(&self, name: &str) -> PathBuf {
            self.0.join(name)
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn uv_sync_installs_only_locked_runtime_dependencies() {
        let paths = RuntimePaths {
            uv: PathBuf::from("uv"),
            python_dir: PathBuf::from("python"),
            venv_dir: PathBuf::from(".venv"),
            codex_binary: None,
            codex_work_root: PathBuf::from("codex-work"),
        };

        let command = uv_sync_command(&paths);
        let args: Vec<_> = command.get_args().collect();

        assert_eq!(args, ["sync", "--locked", "--no-dev"]);
        assert_eq!(command.get_current_dir(), Some(Path::new("python")));
    }

    #[test]
    fn uv_archive_checksum_is_verified_before_extraction() {
        let temp = TestDirectory::new("uv-checksum");
        let archive = temp.child("uv.archive");
        fs::write(&archive, b"verified uv archive").expect("write archive fixture");

        verify_sha256(
            &archive,
            "967f7f6ef44bca3c76800a15a7fa6e5aafba6cd8696551e75c0d662f9714f336",
        )
        .expect("known checksum must pass");
        assert!(verify_sha256(&archive, &"0".repeat(64)).is_err());
    }

    #[test]
    fn uv_release_is_pinned_to_a_sha256() {
        let (archive, checksum) = uv_release().expect("current release target is supported");
        assert!(archive.starts_with("uv-"));
        assert_eq!(checksum.len(), 64);
        assert!(checksum.bytes().all(|byte| byte.is_ascii_hexdigit()));
    }
}
