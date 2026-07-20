use sha2::{Digest, Sha256};
#[cfg(unix)]
use std::cmp::Ordering;
use std::ffi::{OsStr, OsString};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;
use tauri::{AppHandle, Manager};
use wait_timeout::ChildExt;

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
const CODEX_WORK_ROOT_DIR: &str = "codex-work";
/// NVM バージョンディレクトリから解決対象にする最大数。名前順を固定して新しい版を優先する。
#[cfg(unix)]
const MAX_NVM_VERSION_DIRECTORIES: usize = 64;
/// GUI の継承 PATH は無制限に信頼せず、探索するディレクトリ数を制限する。
const MAX_PATH_DIRECTORIES: usize = 64;
/// canonicalize 後に `--version` を実行する候補数の上限。起動遅延を 4 秒以内に保つ。
const MAX_CODEX_CANDIDATES: usize = 8;
/// 不正または壊れた候補が起動を遅延させないための `codex --version` 制限時間（ミリ秒）。
const CODEX_VERSION_TIMEOUT_MILLIS: u64 = 500;

pub struct RuntimePaths {
    pub uv: PathBuf,
    pub python_dir: PathBuf,
    pub venv_dir: PathBuf,
    /// PATH から解決・canonicalize した公式 Codex CLI。
    /// 未導入時は None のまま backend を起動し、Python 側で not_installed とする。
    pub codex_binary: Option<PathBuf>,
    /// Codex が会議データを扱う一時 cwd を作るための app-local 専用ルート。
    pub codex_work_root: PathBuf,
}

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

fn download_uv(destination: &Path) -> Result<(), AppError> {
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
fn codex_binary_names() -> &'static [&'static str] {
    #[cfg(windows)]
    {
        &["codex.exe"]
    }
    #[cfg(not(windows))]
    {
        &["codex"]
    }
}

fn is_executable_file(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        return path
            .metadata()
            .map(|metadata| metadata.permissions().mode() & 0o111 != 0)
            .unwrap_or(false);
    }

    #[cfg(not(unix))]
    true
}

#[derive(Debug, Clone)]
struct CodexSearchEnvironment {
    path: Option<OsString>,
    pnpm_home: Option<PathBuf>,
    volta_home: Option<PathBuf>,
    #[cfg(unix)]
    home: Option<PathBuf>,
    #[cfg(unix)]
    nvm_dir: Option<PathBuf>,
    #[cfg(windows)]
    local_app_data: Option<PathBuf>,
    #[cfg(windows)]
    app_data: Option<PathBuf>,
}

impl CodexSearchEnvironment {
    fn collect() -> Self {
        Self {
            path: std::env::var_os("PATH"),
            pnpm_home: std::env::var_os("PNPM_HOME").map(PathBuf::from),
            volta_home: std::env::var_os("VOLTA_HOME").map(PathBuf::from),
            #[cfg(unix)]
            home: std::env::var_os("HOME").map(PathBuf::from),
            #[cfg(unix)]
            nvm_dir: std::env::var_os("NVM_DIR").map(PathBuf::from),
            #[cfg(windows)]
            local_app_data: std::env::var_os("LOCALAPPDATA").map(PathBuf::from),
            #[cfg(windows)]
            app_data: std::env::var_os("APPDATA").map(PathBuf::from),
        }
    }
}

/// PATH と公式インストーラーの既知の配置を、重複のない絶対ディレクトリ列へ変換する純粋関数。
///
/// ファイルシステム探索は呼び出し側で済ませるため、環境入力だけで単体テストできる。
fn codex_candidate_directories(
    environment: &CodexSearchEnvironment,
    nvm_bin_directories: impl IntoIterator<Item = PathBuf>,
) -> Vec<PathBuf> {
    let mut directories = Vec::new();

    if let Some(path) = &environment.path {
        for directory in std::env::split_paths(path).take(MAX_PATH_DIRECTORIES) {
            push_unique_absolute_directory(&mut directories, directory);
        }
    }

    #[cfg(unix)]
    if let Some(home) = &environment.home {
        for directory in [
            home.join(".local/bin"),
            home.join(".volta/bin"),
            home.join(".npm-global/bin"),
            home.join(".local/share/pnpm"),
            home.join(".bun/bin"),
        ] {
            push_unique_absolute_directory(&mut directories, directory);
        }
    }

    #[cfg(unix)]
    for directory in [
        PathBuf::from("/opt/homebrew/bin"),
        PathBuf::from("/usr/local/bin"),
    ] {
        push_unique_absolute_directory(&mut directories, directory);
    }

    #[cfg(windows)]
    {
        // Codex の公式 Windows ドキュメントは Windows 11 + WSL2 を前提にしている。
        // GUI からの既存導入も検出できるよう、公式 installer と npm の標準配置だけを調べる。
        if let Some(local_app_data) = &environment.local_app_data {
            push_unique_absolute_directory(
                &mut directories,
                local_app_data.join("Programs/OpenAI/Codex/bin"),
            );
            push_unique_absolute_directory(&mut directories, local_app_data.join("Volta/bin"));
        }
        if let Some(app_data) = &environment.app_data {
            push_unique_absolute_directory(&mut directories, app_data.join("npm"));
        }
    }

    if let Some(volta_home) = &environment.volta_home {
        push_unique_absolute_directory(&mut directories, volta_home.join("bin"));
    }
    if let Some(pnpm_home) = &environment.pnpm_home {
        push_unique_absolute_directory(&mut directories, pnpm_home.clone());
    }
    for directory in nvm_bin_directories {
        push_unique_absolute_directory(&mut directories, directory);
    }

    directories
}

fn push_unique_absolute_directory(directories: &mut Vec<PathBuf>, directory: PathBuf) {
    if directory.is_absolute() && !directories.iter().any(|existing| existing == &directory) {
        directories.push(directory);
    }
}

#[cfg(unix)]
fn nvm_bin_directories(environment: &CodexSearchEnvironment) -> Vec<PathBuf> {
    let mut version_roots = Vec::new();
    if let Some(nvm_dir) = &environment.nvm_dir {
        push_unique_absolute_directory(&mut version_roots, nvm_dir.join("versions/node"));
    }
    if let Some(home) = &environment.home {
        push_unique_absolute_directory(&mut version_roots, home.join(".nvm/versions/node"));
    }

    let mut directories = Vec::new();
    for versions_root in version_roots {
        if directories.len() == MAX_NVM_VERSION_DIRECTORIES {
            break;
        }
        let Ok(entries) = std::fs::read_dir(versions_root) else {
            continue;
        };
        let mut entries: Vec<_> = entries.flatten().collect();
        entries.sort_by(|left, right| {
            let left_name = left.file_name();
            let right_name = right.file_name();
            compare_nvm_version_names(&right_name, &left_name)
                .then_with(|| right.path().cmp(&left.path()))
        });
        directories.extend(
            entries
                .into_iter()
                .take(MAX_NVM_VERSION_DIRECTORIES - directories.len())
                .map(|entry| entry.path().join("bin")),
        );
    }

    directories
}

#[cfg(unix)]
fn compare_nvm_version_names(left: &OsStr, right: &OsStr) -> Ordering {
    let left = left.to_string_lossy();
    let right = right.to_string_lossy();
    let mut left_components = left.trim_start_matches('v').split('.');
    let mut right_components = right.trim_start_matches('v').split('.');

    loop {
        match (left_components.next(), right_components.next()) {
            (Some(left), Some(right)) => {
                let ordering = compare_nvm_version_component(left, right);
                if ordering != Ordering::Equal {
                    return ordering;
                }
            }
            (Some(_), None) => return Ordering::Greater,
            (None, Some(_)) => return Ordering::Less,
            (None, None) => return Ordering::Equal,
        }
    }
}

#[cfg(unix)]
fn compare_nvm_version_component(left: &str, right: &str) -> Ordering {
    let left_digits = left.trim_end_matches(|character: char| !character.is_ascii_digit());
    let right_digits = right.trim_end_matches(|character: char| !character.is_ascii_digit());
    let left_suffix = &left[left_digits.len()..];
    let right_suffix = &right[right_digits.len()..];

    left_digits
        .len()
        .cmp(&right_digits.len())
        .then_with(|| left_digits.cmp(right_digits))
        .then_with(|| match (left_suffix.is_empty(), right_suffix.is_empty()) {
            (true, false) => Ordering::Greater,
            (false, true) => Ordering::Less,
            _ => left_suffix.cmp(right_suffix),
        })
}

#[cfg(not(unix))]
fn nvm_bin_directories(_: &CodexSearchEnvironment) -> Vec<PathBuf> {
    Vec::new()
}

fn canonical_codex_candidates(directories: impl IntoIterator<Item = PathBuf>) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    for directory in directories {
        if !directory.is_absolute() {
            continue;
        }
        for name in codex_binary_names() {
            let candidate = directory.join(name);
            if !is_executable_file(&candidate) {
                continue;
            }
            let Ok(candidate) = std::fs::canonicalize(candidate) else {
                continue;
            };
            if candidate.is_absolute()
                && is_executable_file(&candidate)
                && !candidates.iter().any(|existing| existing == &candidate)
            {
                candidates.push(candidate);
                if candidates.len() == MAX_CODEX_CANDIDATES {
                    return candidates;
                }
            }
        }
    }

    candidates
}

fn parse_codex_release(value: &str) -> Option<(u64, u64, u64)> {
    let mut components = value.trim_start_matches('v').split('.');
    let major = components.next()?.parse().ok()?;
    let minor = components.next()?.parse().ok()?;
    let patch = components.next()?.parse().ok()?;
    if components.next().is_some() {
        return None;
    }
    Some((major, minor, patch))
}

fn codex_version_priority(candidate: &Path) -> Option<u8> {
    let mut child = std::process::Command::new(candidate)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let status = match child.wait_timeout(Duration::from_millis(CODEX_VERSION_TIMEOUT_MILLIS)) {
        Ok(Some(status)) => status,
        Ok(None) | Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
    };
    if !status.success() {
        return None;
    }

    let mut output = Vec::new();
    child.stdout.take()?.read_to_end(&mut output).ok()?;
    let output = String::from_utf8_lossy(&output);
    let mut tokens = output.split_whitespace();
    while let Some(token) = tokens.next() {
        if token != "codex-cli" {
            continue;
        }
        let version = parse_codex_release(tokens.next()?)?;
        return match version {
            (0, 144, 1) => Some(3),
            (0, 144, 0) => Some(2),
            version if version >= (0, 144, 0) => Some(1),
            _ => None,
        };
    }
    None
}

fn find_codex_in_directories(directories: impl IntoIterator<Item = PathBuf>) -> Option<PathBuf> {
    let candidates = canonical_codex_candidates(directories);
    let fallback = candidates.first().cloned()?;
    let mut verified_fallback = None;
    let mut compatible_unverified = None;

    for candidate in candidates {
        match codex_version_priority(&candidate) {
            Some(3) => return Some(candidate),
            Some(2) if verified_fallback.is_none() => verified_fallback = Some(candidate),
            Some(1) if compatible_unverified.is_none() => compatible_unverified = Some(candidate),
            _ => {}
        }
    }

    verified_fallback
        .or(compatible_unverified)
        .or(Some(fallback))
}

fn find_codex_from_environment(environment: &CodexSearchEnvironment) -> Option<PathBuf> {
    let nvm_directories = nvm_bin_directories(environment);
    find_codex_in_directories(codex_candidate_directories(environment, nvm_directories))
}

/// 親環境を収集し、既知の公式インストール先と PATH から Codex CLI を解決する。
///
/// CODEX_BINARY など親環境の任意 override は信頼せず、CLI 未導入はエラーにしない。
/// backend 自体を起動して route から not_installed を返せるようにするためである。
pub fn find_codex() -> Option<PathBuf> {
    find_codex_from_environment(&CodexSearchEnvironment::collect())
}

fn create_codex_work_root(app_local_data_dir: &Path) -> Result<PathBuf, AppError> {
    let work_root = app_local_data_dir.join(CODEX_WORK_ROOT_DIR);
    if std::fs::symlink_metadata(&work_root)
        .map(|metadata| metadata.file_type().is_symlink())
        .unwrap_or(false)
    {
        return Err(AppError::Process(format!(
            "Codex work root must not be a symlink: {}",
            work_root.display()
        )));
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::{DirBuilderExt, PermissionsExt};

        std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(&work_root)?;
        std::fs::set_permissions(&work_root, std::fs::Permissions::from_mode(0o700))?;
    }
    #[cfg(not(unix))]
    std::fs::create_dir_all(&work_root)?;

    let trusted_app_data_dir = std::fs::canonicalize(app_local_data_dir)?;
    let trusted_work_root = std::fs::canonicalize(work_root)?;
    if trusted_work_root.parent() != Some(trusted_app_data_dir.as_path()) {
        return Err(AppError::Process(format!(
            "Codex work root escaped app-local data directory: {}",
            trusted_work_root.display()
        )));
    }

    Ok(trusted_work_root)
}

pub fn resolve_runtime_paths(app: &AppHandle) -> Result<RuntimePaths, AppError> {
    let resource_dir = app.path().resource_dir()?;

    let app_local_data_dir = app.path().app_local_data_dir()?;
    let managed_uv = managed_uv_path(&app_local_data_dir);
    let uv = find_uv(&managed_uv);
    if !uv.exists() {
        download_uv(&uv)?;
    }

    let python_dir = resource_dir.join("python");
    if !python_dir.exists() {
        return Err(format!(
            "python ディレクトリが見つかりません: {}",
            python_dir.display()
        )
        .into());
    }

    let venv_dir = app_local_data_dir.join(".venv");
    let codex_binary = find_codex();
    let codex_work_root = create_codex_work_root(&app_local_data_dir)?;

    Ok(RuntimePaths {
        uv,
        python_dir,
        venv_dir,
        codex_binary,
        codex_work_root,
    })
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
            // sleep に入る前に「リトライ待機中」のメッセージを通知しておき、
            // フロントエンドの進捗表示が sleep 中に古く残らないようにする。
            let waiting_message = format!(
                "uv sync failed. Retrying in {}s... (attempt {}/{})",
                delay.as_secs(),
                attempt - 1,
                MAX_RETRIES
            );
            println!("[backend] {waiting_message}");
            let _ = set_bootstrap_status(app, "syncing", waiting_message);
            std::thread::sleep(delay);

            // sleep 完了直後に次の attempt の開始メッセージを通知し、
            // ユーザーから見ると sleep から復帰してすぐ再試行が始まることを示す。
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

    // MAX_RETRIES = 3 の範囲で到達不能だが、for ループの型チェックのために必要。
    Err(AppError::Process(
        "uv sync に失敗しました（リトライ上限到達）".into(),
    ))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    #[cfg(unix)]
    use std::os::unix::fs::{symlink, PermissionsExt};
    use std::sync::atomic::{AtomicUsize, Ordering as AtomicOrdering};

    static TEST_DIRECTORY_SEQUENCE: AtomicUsize = AtomicUsize::new(0);

    struct TestDirectory(PathBuf);

    impl TestDirectory {
        fn new(label: &str) -> Self {
            let sequence = TEST_DIRECTORY_SEQUENCE.fetch_add(1, AtomicOrdering::Relaxed);
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

    #[cfg(unix)]
    fn fake_codex(directory: &Path, version: &str) -> PathBuf {
        fs::create_dir_all(directory).expect("create fake Codex directory");
        let executable = directory.join("codex");
        fs::write(
            &executable,
            format!("#!/bin/sh\nprintf '%s\\n' 'codex-cli v{version}'\n"),
        )
        .expect("write fake Codex executable");
        let mut permissions = fs::metadata(&executable)
            .expect("read fake Codex permissions")
            .permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&executable, permissions).expect("make fake Codex executable");
        executable
    }

    #[cfg(unix)]
    fn hanging_codex(directory: &Path) -> (PathBuf, PathBuf) {
        fs::create_dir_all(directory).expect("create hanging Codex directory");
        let executable = directory.join("codex");
        let pid_file = executable.with_extension("pid");
        fs::write(
            &executable,
            "#!/bin/sh\nprintf '%s' \"$$\" > \"$0.pid\"\nwhile :; do :; done\n",
        )
        .expect("write hanging Codex executable");
        let mut permissions = fs::metadata(&executable)
            .expect("read hanging Codex permissions")
            .permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&executable, permissions).expect("make hanging Codex executable");
        (executable, pid_file)
    }

    #[cfg(unix)]
    fn search_environment(
        path_directories: &[PathBuf],
        home: &Path,
        nvm_dir: Option<PathBuf>,
    ) -> CodexSearchEnvironment {
        CodexSearchEnvironment {
            path: Some(std::env::join_paths(path_directories).expect("serialize test PATH")),
            pnpm_home: None,
            volta_home: None,
            home: Some(home.to_path_buf()),
            nvm_dir,
        }
    }

    #[cfg(unix)]
    fn canonical(path: &Path) -> PathBuf {
        fs::canonicalize(path).expect("canonicalize fake executable")
    }

    #[cfg(unix)]
    #[test]
    fn finds_verified_official_home_install_when_gui_path_is_empty() {
        let temp = TestDirectory::new("empty-path-home");
        let official_codex = fake_codex(&temp.child("home/.local/bin"), "0.144.1");
        let environment = search_environment(&[], &temp.child("home"), None);

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&official_codex)),
            "a GUI with an empty PATH must still discover the official HOME installation"
        );
    }

    #[cfg(unix)]
    #[test]
    fn selects_verified_official_install_over_incompatible_path_candidate() {
        let temp = TestDirectory::new("stale-path");
        let stale_codex = fake_codex(&temp.child("stale-path"), "0.143.9");
        let official_codex = fake_codex(&temp.child("home/.local/bin"), "0.144.1");
        let environment = search_environment(
            &[stale_codex.parent().unwrap().to_path_buf()],
            &temp.child("home"),
            None,
        );

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&official_codex)),
            "an incompatible PATH executable must not mask a verified official installation"
        );
    }

    #[cfg(unix)]
    #[test]
    fn prefers_0_144_1_over_0_144_0_regardless_of_directory_order() {
        let temp = TestDirectory::new("version-priority");
        let fallback = fake_codex(&temp.child("path-0-144-0"), "0.144.0");
        let preferred = fake_codex(&temp.child("home/.local/bin"), "0.144.1");
        let environment = search_environment(
            &[fallback.parent().unwrap().to_path_buf()],
            &temp.child("home"),
            None,
        );

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&preferred)),
            "the verified 0.144.1 protocol revision is authoritative after 0.144.0"
        );
    }

    #[cfg(unix)]
    #[test]
    fn selects_later_compatible_stable_release_after_incompatible_path_candidate() {
        let temp = TestDirectory::new("stable-after-incompatible");
        let incompatible = fake_codex(&temp.child("path-0-143"), "0.143.9");
        let compatible = fake_codex(&temp.child("path-0-145"), "0.145.0");
        let environment = search_environment(
            &[
                incompatible.parent().unwrap().to_path_buf(),
                compatible.parent().unwrap().to_path_buf(),
            ],
            &temp.child("home"),
            None,
        );

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&compatible)),
            "a later stable compatible release must outrank an earlier incompatible candidate"
        );
    }

    #[cfg(unix)]
    #[test]
    fn does_not_treat_a_0_145_prerelease_as_a_compatible_stable_release() {
        let temp = TestDirectory::new("prerelease-is-incompatible");
        let prerelease = fake_codex(&temp.child("path-0-145-prerelease"), "0.145.0-beta.1");
        let verified = fake_codex(&temp.child("path-0-144-0"), "0.144.0");
        let environment = search_environment(
            &[
                prerelease.parent().unwrap().to_path_buf(),
                verified.parent().unwrap().to_path_buf(),
            ],
            &temp.child("home"),
            None,
        );

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&verified)),
            "a prerelease must not outrank a verified stable Codex release"
        );
    }

    #[cfg(unix)]
    #[test]
    fn prefers_verified_0_144_1_over_a_newer_compatible_stable_release() {
        let temp = TestDirectory::new("verified-over-newer-compatible");
        let compatible = fake_codex(&temp.child("path-0-145"), "0.145.0");
        let verified = fake_codex(&temp.child("path-0-144-1"), "0.144.1");
        let environment = search_environment(
            &[
                compatible.parent().unwrap().to_path_buf(),
                verified.parent().unwrap().to_path_buf(),
            ],
            &temp.child("home"),
            None,
        );

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&verified)),
            "the verified 0.144.1 release must outrank a newer compatible stable release"
        );
    }

    #[cfg(unix)]
    #[test]
    fn falls_back_to_first_0_144_0_when_no_0_144_1_is_available() {
        let temp = TestDirectory::new("version-fallback");
        let fallback = fake_codex(&temp.child("fallback"), "0.144.0");
        let mut path_directories = vec![fallback.parent().unwrap().to_path_buf()];
        for index in 0..7 {
            let incompatible = fake_codex(&temp.child(&format!("incompatible-{index}")), "0.143.9");
            path_directories.push(incompatible.parent().unwrap().to_path_buf());
        }
        let environment = search_environment(&path_directories, &temp.child("home"), None);

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&fallback)),
            "0.144.0 remains usable when the preferred 0.144.1 protocol is unavailable"
        );
    }

    #[cfg(unix)]
    #[test]
    fn returns_first_executable_when_all_discovered_versions_are_incompatible() {
        let temp = TestDirectory::new("incompatible-fallback");
        let first = fake_codex(&temp.child("first"), "0.143.9");
        let mut path_directories = vec![first.parent().unwrap().to_path_buf()];
        for index in 0..7 {
            let incompatible = fake_codex(&temp.child(&format!("incompatible-{index}")), "0.142.0");
            path_directories.push(incompatible.parent().unwrap().to_path_buf());
        }
        let environment = search_environment(&path_directories, &temp.child("home"), None);

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&first)),
            "a detected executable is retained for diagnostics even when no compatible version is found"
        );
    }

    #[cfg(unix)]
    #[test]
    fn deduplicates_canonical_candidates_before_applying_the_eight_candidate_limit() {
        let temp = TestDirectory::new("deduplicate-candidates");
        let shared = fake_codex(&temp.child("shared"), "0.143.9");
        let mut path_directories = Vec::new();
        for index in 0..8 {
            let directory = temp.child(&format!("alias-{index}"));
            fs::create_dir_all(&directory).expect("create alias directory");
            symlink(&shared, directory.join("codex")).expect("create duplicate Codex symlink");
            path_directories.push(directory);
        }
        let verified = fake_codex(&temp.child("verified"), "0.144.1");
        path_directories.push(verified.parent().unwrap().to_path_buf());
        let environment = search_environment(&path_directories, &temp.child("home"), None);

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&verified)),
            "aliases for one executable must not consume the bounded candidate budget"
        );
    }

    #[cfg(unix)]
    #[test]
    fn stops_after_eight_distinct_candidates() {
        let temp = TestDirectory::new("candidate-limit");
        let mut path_directories = Vec::new();
        let mut first = None;
        for index in 0..8 {
            let executable = fake_codex(&temp.child(&format!("incompatible-{index}")), "0.143.9");
            if first.is_none() {
                first = Some(executable.clone());
            }
            path_directories.push(executable.parent().unwrap().to_path_buf());
        }
        let ignored_verified = fake_codex(&temp.child("ignored-verified"), "0.144.1");
        path_directories.push(ignored_verified.parent().unwrap().to_path_buf());
        let environment = search_environment(&path_directories, &temp.child("home"), None);

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(first.as_ref().expect("first candidate"))),
            "the ninth distinct executable must not be probed after the eight-candidate cap"
        );
    }

    #[cfg(unix)]
    #[test]
    fn selects_newest_nvm_version_when_verified_versions_tie() {
        let temp = TestDirectory::new("nvm-order");
        let nvm_root = temp.child("nvm");
        fake_codex(&nvm_root.join("versions/node/v20.10.0/bin"), "0.144.1");
        let newest = fake_codex(&nvm_root.join("versions/node/v22.1.0/bin"), "0.144.1");
        let environment = search_environment(&[], &temp.child("home"), Some(nvm_root));

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&newest)),
            "NVM discovery must deterministically prefer the numerically newest Node version"
        );
    }

    #[cfg(unix)]
    #[test]
    fn kills_and_reaps_a_timed_out_candidate_before_finding_a_verified_one() {
        let temp = TestDirectory::new("timeout-reap");
        let (hung, pid_file) = hanging_codex(&temp.child("hung"));
        let verified = fake_codex(&temp.child("verified"), "0.144.1");
        let environment = search_environment(
            &[
                hung.parent().unwrap().to_path_buf(),
                verified.parent().unwrap().to_path_buf(),
            ],
            &temp.child("home"),
            None,
        );

        assert_eq!(
            find_codex_from_environment(&environment),
            Some(canonical(&verified)),
            "a hanging version probe must not prevent discovery of a later verified executable"
        );
        #[cfg(target_os = "linux")]
        {
            let process_id = fs::read_to_string(&pid_file)
                .expect("hanging candidate recorded its process id")
                .parse::<u32>()
                .expect("hanging candidate process id is numeric");
            assert!(
                !Path::new(&format!("/proc/{process_id}")).exists(),
                "the timed-out candidate process must be reaped before resolution returns"
            );
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
    fn managed_uv_path_is_version_scoped() {
        let path = managed_uv_path(Path::new("app-data"));
        assert!(path.starts_with(Path::new("app-data/runtime/uv-0.11.7")));
        #[cfg(target_os = "windows")]
        assert_eq!(path.file_name(), Some(OsStr::new("uv.exe")));
        #[cfg(not(target_os = "windows"))]
        assert_eq!(path.file_name(), Some(OsStr::new("uv")));
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

    #[test]
    fn compute_backoff_delay_first_attempt() {
        assert_eq!(compute_backoff_delay(1), Duration::from_secs(1));
    }

    #[test]
    fn compute_backoff_delay_zero_returns_initial() {
        // attempt = 0 は呼び出し規約上想定外 (`attempt >= 1` 想定) だが、
        // saturating_sub により 0.saturating_sub(1) = 0 となり、
        // 2u64.saturating_pow(0) = 1 が掛けられるため結果は BACKOFF_INITIAL_SECS (=1s) になる。
        // この飽和演算による境界挙動をテストで固定する。
        assert_eq!(compute_backoff_delay(0), Duration::from_secs(1));
    }

    #[test]
    fn compute_backoff_delay_doubles_each_attempt() {
        assert_eq!(compute_backoff_delay(2), Duration::from_secs(2));
        assert_eq!(compute_backoff_delay(3), Duration::from_secs(4));
    }

    #[test]
    fn compute_backoff_delay_is_capped() {
        assert_eq!(compute_backoff_delay(4), Duration::from_secs(8));
        assert_eq!(compute_backoff_delay(10), Duration::from_secs(8));
    }
}
