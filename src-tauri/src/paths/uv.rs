use sha2::{Digest, Sha256};
use std::ffi::OsStr;
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
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
const SYNC_STAMP_VERSION: &str = "meeting-supporter-uv-sync-v1";
const SYNC_STAMP_FILE: &str = ".venv-sync-success";
static SYNC_STAMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

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
fn expected_python_executable(venv_dir: &Path) -> PathBuf {
    #[cfg(target_os = "windows")]
    return venv_dir.join("Scripts").join("python.exe");

    #[cfg(not(target_os = "windows"))]
    venv_dir.join("bin").join("python")
}

fn sync_stamp_path(venv_dir: &Path) -> Result<PathBuf, AppError> {
    let parent = venv_dir
        .parent()
        .ok_or_else(|| AppError::Process("Python environment has no parent directory".into()))?;
    Ok(parent.join(SYNC_STAMP_FILE))
}

fn hash_file(path: &Path) -> Result<[u8; 32], AppError> {
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
    Ok(hasher.finalize().into())
}

fn add_fingerprint_component(hasher: &mut Sha256, name: &str, value: &[u8]) {
    hasher.update((name.len() as u64).to_le_bytes());
    hasher.update(name.as_bytes());
    hasher.update((value.len() as u64).to_le_bytes());
    hasher.update(value);
}

fn sync_fingerprint(paths: &RuntimePaths) -> Result<Option<String>, AppError> {
    let app_local_data_dir = paths
        .venv_dir
        .parent()
        .ok_or_else(|| AppError::Process("Python environment has no parent directory".into()))?;
    let expected_managed_uv = managed_uv_path(app_local_data_dir);
    let actual_uv =
        std::fs::canonicalize(&paths.uv).unwrap_or_else(|_| paths.uv.clone());
    let managed_uv = std::fs::canonicalize(&expected_managed_uv)
        .unwrap_or(expected_managed_uv);
    if actual_uv != managed_uv {
        return Ok(None);
    }

    let (uv_archive, uv_archive_checksum) = uv_release()?;
    let project_hash = hash_file(&paths.python_dir.join("pyproject.toml"))?;
    let lock_hash = hash_file(&paths.python_dir.join("uv.lock"))?;
    let mut hasher = Sha256::new();

    add_fingerprint_component(&mut hasher, "stamp-version", SYNC_STAMP_VERSION.as_bytes());
    add_fingerprint_component(&mut hasher, "pyproject.toml", &project_hash);
    add_fingerprint_component(&mut hasher, "uv.lock", &lock_hash);
    add_fingerprint_component(&mut hasher, "uv-version", UV_VERSION.as_bytes());
    add_fingerprint_component(&mut hasher, "uv-release-archive", uv_archive.as_bytes());
    add_fingerprint_component(
        &mut hasher,
        "uv-release-checksum",
        uv_archive_checksum.as_bytes(),
    );
    add_fingerprint_component(
        &mut hasher,
        "application-version",
        env!("CARGO_PKG_VERSION").as_bytes(),
    );
    add_fingerprint_component(&mut hasher, "target-os", std::env::consts::OS.as_bytes());
    add_fingerprint_component(&mut hasher, "target-arch", std::env::consts::ARCH.as_bytes());
    add_fingerprint_component(&mut hasher, "sync-arguments", b"sync\0--locked\0--no-dev");
    #[cfg(target_os = "windows")]
    add_fingerprint_component(
        &mut hasher,
        "environment-executable",
        b"Scripts/python.exe",
    );
    #[cfg(not(target_os = "windows"))]
    add_fingerprint_component(&mut hasher, "environment-executable", b"bin/python");

    Ok(Some(format!("{:x}", hasher.finalize())))
}

fn sync_stamp_contents(fingerprint: &str) -> String {
    format!("{SYNC_STAMP_VERSION}\n{fingerprint}\n")
}

fn can_skip_uv_sync(paths: &RuntimePaths, fingerprint: &str, stamp_path: &Path) -> bool {
    if !expected_python_executable(&paths.venv_dir).is_file() {
        return false;
    }

    std::fs::read_to_string(stamp_path)
        .map(|contents| contents == sync_stamp_contents(fingerprint))
        .unwrap_or(false)
}

#[cfg(target_os = "windows")]
fn replace_file_atomically(source: &Path, destination: &Path) -> std::io::Result<()> {
    use std::iter;
    use std::os::windows::ffi::OsStrExt;

    const MOVEFILE_REPLACE_EXISTING: u32 = 0x1;
    const MOVEFILE_WRITE_THROUGH: u32 = 0x8;

    #[link(name = "Kernel32")]
    extern "system" {
        fn MoveFileExW(
            existing_file_name: *const u16,
            new_file_name: *const u16,
            flags: u32,
        ) -> i32;
    }

    let source: Vec<u16> = source.as_os_str().encode_wide().chain(iter::once(0)).collect();
    let destination: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(iter::once(0))
        .collect();
    let result = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(not(target_os = "windows"))]
fn replace_file_atomically(source: &Path, destination: &Path) -> std::io::Result<()> {
    std::fs::rename(source, destination)
}

fn write_sync_stamp_atomic(stamp_path: &Path, fingerprint: &str) -> Result<(), AppError> {
    let parent = stamp_path
        .parent()
        .ok_or_else(|| AppError::Process("uv sync stamp has no parent directory".into()))?;
    let sequence = SYNC_STAMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let temporary_path = parent.join(format!(
        ".venv-sync-success.{}.{}.tmp",
        std::process::id(),
        sequence
    ));

    let write_result = (|| -> std::io::Result<()> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary_path)?;
        file.write_all(sync_stamp_contents(fingerprint).as_bytes())?;
        file.sync_all()?;
        drop(file);
        replace_file_atomically(&temporary_path, stamp_path)?;
        #[cfg(unix)]
        std::fs::File::open(parent)?.sync_all()?;
        Ok(())
    })();

    if write_result.is_err() {
        let _ = std::fs::remove_file(&temporary_path);
    }
    write_result.map_err(AppError::from)
}
fn invalidate_sync_stamp(stamp_path: &Path) -> Result<(), AppError> {
    match std::fs::remove_file(stamp_path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(AppError::Process(format!(
            "uv sync stamp could not be invalidated before environment mutation: {error}"
        ))),
    }
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
/// 入力と環境が前回成功時から完全に一致する場合は同期を省略する。それ以外は
/// `uv sync --locked --no-dev` を最大 3 回リトライし、各試行は 120 秒でタイムアウトする。
/// リトライ間は指数バックオフで待機し、ユーザーに進捗を表示する。
/// すべての試行が失敗した場合は `AppError::Process` を返す。
pub fn ensure_python_environment(app: &AppHandle) -> Result<(), AppError> {
    let paths = resolve_runtime_paths(app)?;

    println!("[backend] uv: {}", paths.uv.display());
    println!("[backend] python_dir: {}", paths.python_dir.display());
    println!("[backend] venv: {}", paths.venv_dir.display());

    let fingerprint = sync_fingerprint(&paths).ok().flatten();
    let stamp_path = sync_stamp_path(&paths.venv_dir)?;
    if fingerprint
        .as_deref()
        .map(|fingerprint| can_skip_uv_sync(&paths, fingerprint, &stamp_path))
        .unwrap_or(false)
    {
        println!("[backend] Python environment is current; skipping uv sync");
        return Ok(());
    }

    // A failed or partial sync must never leave a stamp that could become valid again
    // after inputs are restored.
    invalidate_sync_stamp(&stamp_path)?;

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
                if let (Some(before), Ok(Some(after))) =
                    (fingerprint.as_deref(), sync_fingerprint(&paths))
                {
                    if before == after {
                        if let Err(error) = write_sync_stamp_atomic(&stamp_path, before) {
                            eprintln!("[backend] could not persist the uv sync stamp: {error}");
                        }
                    } else {
                        eprintln!(
                            "[backend] Python environment inputs changed during uv sync; not caching success"
                        );
                    }
                }
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

    fn runtime_fixture(temp: &TestDirectory) -> RuntimePaths {
        let python_dir = temp.child("python");
        let venv_dir = temp.child(".venv");
        let uv = managed_uv_path(&temp.0);
        fs::create_dir_all(&python_dir).expect("create Python project fixture");
        fs::create_dir_all(uv.parent().expect("managed uv parent"))
            .expect("create managed uv directory");
        fs::write(
            python_dir.join("pyproject.toml"),
            b"[project]\nname = \"fixture\"\n",
        )
        .expect("write project fixture");
        fs::write(python_dir.join("uv.lock"), b"version = 1\n").expect("write lock fixture");
        fs::write(&uv, b"fixture uv executable").expect("write uv fixture");

        RuntimePaths {
            uv,
            python_dir,
            venv_dir,
            codex_binary: None,
            codex_work_root: temp.child("codex-work"),
        }
    }

    fn create_environment_executable(paths: &RuntimePaths) {
        let executable = expected_python_executable(&paths.venv_dir);
        fs::create_dir_all(executable.parent().expect("environment executable parent"))
            .expect("create environment executable directory");
        fs::write(executable, b"fixture Python executable")
            .expect("write environment executable");
    }

    #[test]
    fn exact_success_stamp_and_environment_skip_sync() {
        let temp = TestDirectory::new("warm-sync");
        let paths = runtime_fixture(&temp);
        create_environment_executable(&paths);
        let fingerprint = sync_fingerprint(&paths)
            .expect("fingerprint fixture")
            .expect("managed uv is cacheable");
        let stamp = sync_stamp_path(&paths.venv_dir).expect("stamp path");
        write_sync_stamp_atomic(&stamp, &fingerprint).expect("write success stamp");

        assert!(can_skip_uv_sync(&paths, &fingerprint, &stamp));
    }

    #[test]
    fn cold_mismatch_corrupt_and_missing_executable_require_sync() {
        let temp = TestDirectory::new("sync-decisions");
        let paths = runtime_fixture(&temp);
        create_environment_executable(&paths);
        let fingerprint = sync_fingerprint(&paths)
            .expect("fingerprint fixture")
            .expect("managed uv is cacheable");
        let stamp = sync_stamp_path(&paths.venv_dir).expect("stamp path");

        assert!(!can_skip_uv_sync(&paths, &fingerprint, &stamp));

        fs::write(&stamp, b"not a success stamp").expect("write corrupt stamp");
        assert!(!can_skip_uv_sync(&paths, &fingerprint, &stamp));

        write_sync_stamp_atomic(&stamp, &"0".repeat(64)).expect("write mismatched stamp");
        assert!(!can_skip_uv_sync(&paths, &fingerprint, &stamp));

        write_sync_stamp_atomic(&stamp, &fingerprint).expect("write matching stamp");
        fs::remove_file(expected_python_executable(&paths.venv_dir))
            .expect("remove environment executable");
        assert!(!can_skip_uv_sync(&paths, &fingerprint, &stamp));
    }

    #[test]
    fn fingerprint_changes_with_project_inputs_and_uses_pinned_uv_identity() {
        let temp = TestDirectory::new("sync-fingerprint");
        let paths = runtime_fixture(&temp);
        let original = sync_fingerprint(&paths)
            .expect("original fingerprint")
            .expect("managed uv is cacheable");

        fs::write(
            paths.python_dir.join("pyproject.toml"),
            b"[project]\nname = \"changed\"\n",
        )
        .expect("change project fixture");
        let changed_project = sync_fingerprint(&paths)
            .expect("changed project fingerprint")
            .expect("managed uv is cacheable");
        assert_ne!(original, changed_project);

        fs::write(
            paths.python_dir.join("pyproject.toml"),
            b"[project]\nname = \"fixture\"\n",
        )
        .expect("restore project fixture");
        fs::write(paths.python_dir.join("uv.lock"), b"version = 2\n")
            .expect("change lock fixture");
        let changed_lock = sync_fingerprint(&paths)
            .expect("changed lock fingerprint")
            .expect("managed uv is cacheable");
        assert_ne!(original, changed_lock);

        fs::write(paths.python_dir.join("uv.lock"), b"version = 1\n")
            .expect("restore lock fixture");
        fs::write(&paths.uv, b"changed bytes are not read on warm start")
            .expect("change managed uv fixture");
        assert_eq!(
            original,
            sync_fingerprint(&paths)
                .expect("pinned managed uv fingerprint")
                .expect("managed uv is cacheable")
        );

        let system_uv = temp.child(if cfg!(target_os = "windows") {
            "system-uv.exe"
        } else {
            "system-uv"
        });
        fs::write(&system_uv, b"arbitrary system uv").expect("write system uv fixture");
        let system_paths = RuntimePaths {
            uv: system_uv,
            python_dir: paths.python_dir.clone(),
            venv_dir: paths.venv_dir.clone(),
            codex_binary: None,
            codex_work_root: paths.codex_work_root.clone(),
        };
        assert_eq!(
            sync_fingerprint(&system_paths).expect("classify system uv"),
            None
        );
    }

    #[test]
    fn atomic_stamp_write_replaces_previous_success() {
        let temp = TestDirectory::new("replace-sync-stamp");
        let paths = runtime_fixture(&temp);
        let stamp = sync_stamp_path(&paths.venv_dir).expect("stamp path");
        let first = "1".repeat(64);
        let second = "2".repeat(64);

        write_sync_stamp_atomic(&stamp, &first).expect("write first stamp");
        write_sync_stamp_atomic(&stamp, &second).expect("replace stamp");

        assert_eq!(
            fs::read_to_string(stamp).expect("read replaced stamp"),
            sync_stamp_contents(&second)
        );
    }

    #[test]
    fn invalidating_before_a_failed_sync_leaves_no_valid_stamp() {
        let temp = TestDirectory::new("failed-sync-stamp");
        let paths = runtime_fixture(&temp);
        create_environment_executable(&paths);
        let fingerprint = sync_fingerprint(&paths)
            .expect("fingerprint fixture")
            .expect("managed uv is cacheable");
        let stamp = sync_stamp_path(&paths.venv_dir).expect("stamp path");
        write_sync_stamp_atomic(&stamp, &fingerprint).expect("write prior stamp");

        invalidate_sync_stamp(&stamp).expect("invalidate prior stamp");
        // A failed sync performs no success-stamp write.

        assert!(!can_skip_uv_sync(&paths, &fingerprint, &stamp));
        assert!(!stamp.exists());
    }

    #[test]
    fn invalidation_failure_aborts_before_environment_mutation() {
        let temp = TestDirectory::new("stamp-invalidation-failure");
        let stamp = temp.child(SYNC_STAMP_FILE);
        fs::create_dir(&stamp).expect("create non-removable stamp fixture");

        assert!(invalidate_sync_stamp(&stamp).is_err());
        assert!(stamp.is_dir());
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
