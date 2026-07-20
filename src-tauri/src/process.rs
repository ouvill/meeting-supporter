#[cfg(windows)]
use process_wrap::std::JobObject;
#[cfg(unix)]
use process_wrap::std::ProcessGroup;
use process_wrap::std::{ChildWrapper, CommandWrap};
use rand::{distributions::Alphanumeric, rngs::OsRng, Rng};
use serde::Serialize;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::process::{Command, ExitStatus};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager};

use crate::error::AppError;
use crate::paths::resolve_runtime_paths;

/// バックエンドプロセスの異常終了に関する診断情報。
/// フロントエンドへの通知および開発者向けログ出力に利用する。
#[derive(Clone, Debug, Serialize)]
pub struct BackendCrashInfo {
    /// 予期しない終了だったかどうか。
    /// `kill_backend` による意図的なシャットダウンでは `false`。
    pub unexpected: bool,
    /// プロセスの終了コード（Unix でシグナルによる終了の場合は `None`）。
    pub exit_code: Option<i32>,
    /// 終了シグナル番号（Unix 以外や不明の場合は `None`）。
    pub signal: Option<i32>,
    /// ユーザー・開発者向けのメッセージ（英語/日本語混合可）。
    pub message: String,
}

/// アプリ状態：FastAPI サブプロセスを保持。
/// process-wrap により Unix はプロセスグループ、Windows は Job Object で
/// 孫プロセスも含めたプロセスツリーを一括 kill する。
pub struct BackendProcess {
    pub child: Option<Box<dyn ChildWrapper + Send>>,
    pub port: Option<u16>,
    /// Capability token required by the local FastAPI backend.
    pub auth_token: Option<String>,
    /// Rust-only capability for the process-memory managed session bridge.
    pub managed_session_capability: Option<String>,
    /// `kill_backend` 等による意図的なシャットダウン済みフラグ。
    /// `true` の場合はプロセス終了を検知してもクラッシュ情報を作成しない。
    pub intentional_shutdown: bool,
    /// 直近の異常終了情報。正常稼働中・未検出時は `None`。
    pub crash_info: Option<BackendCrashInfo>,
}

impl BackendProcess {
    pub fn none() -> Self {
        BackendProcess {
            child: None,
            port: None,
            auth_token: None,
            managed_session_capability: None,
            intentional_shutdown: false,
            crash_info: None,
        }
    }
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.port = None;
    }
}

pub type BackendState = Mutex<BackendProcess>;

/// Unix シグナル番号を人間が読める名前に変換する（代表的なシグナルのみ）。
#[cfg(unix)]
fn signal_name(sig: i32) -> &'static str {
    match sig {
        1 => "SIGHUP",
        2 => "SIGINT",
        3 => "SIGQUIT",
        6 => "SIGABRT",
        9 => "SIGKILL",
        11 => "SIGSEGV",
        13 => "SIGPIPE",
        14 => "SIGALRM",
        15 => "SIGTERM",
        24 => "SIGXCPU",
        25 => "SIGXFSZ",
        30 => "SIGUSR1",
        31 => "SIGUSR2",
        _ => "unknown signal",
    }
}

/// `ExitStatus` から終了コードとシグナルを抽出し、説明メッセージを生成する。
///
/// この関数は純粋関数であり、テスト容易性のために分離されている。
pub fn describe_exit_status(status: ExitStatus) -> (Option<i32>, Option<i32>, String) {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        let code = status.code();
        let sig = status.signal();
        let description = match (code, sig) {
            (Some(c), _) => format!("Exited with code {c}"),
            (_, Some(s)) => format!("Killed by signal {s} ({})", signal_name(s)),
            _ => "Exited with unknown status".into(),
        };
        (code, sig, description)
    }
    #[cfg(not(unix))]
    {
        let code = status.code();
        let description = match code {
            Some(c) => format!("Exited with code {c}"),
            None => "Exited with unknown status".into(),
        };
        (code, None, description)
    }
}

/// クラッシュ情報を生成する。
/// 純粋関数であり、テスト容易性のために分離されている。
pub fn build_crash_info(status: ExitStatus) -> BackendCrashInfo {
    let (exit_code, signal, description) = describe_exit_status(status);
    BackendCrashInfo {
        unexpected: true,
        exit_code,
        signal,
        message: format!("Backend process terminated unexpectedly. {description}."),
    }
}

/// バックエンドプロセスの状態を更新し、生存かどうかを返す。
///
/// プロセスが終了していた場合：
/// - 意図的なシャットダウン（`intentional_shutdown == true`）の場合は
///   クラッシュ情報を記録しない。
/// - 予期しない終了の場合は `BackendCrashInfo` を生成・保持し、
///   コンソールにエラーログを出力する。
pub fn refresh_backend_state(backend: &mut BackendProcess) -> bool {
    if let Some(child) = backend.child.as_mut() {
        match child.try_wait() {
            Ok(None) => true,
            Ok(Some(status)) => {
                if !backend.intentional_shutdown {
                    let crash = build_crash_info(status);
                    eprintln!("[backend] ERROR: {}", crash.message);
                    backend.crash_info = Some(crash);
                }
                backend.child = None;
                backend.port = None;
                false
            }
            Err(e) => {
                eprintln!("[backend] try_wait failed: {e}");
                false
            }
        }
    } else {
        false
    }
}

pub fn allocate_backend_port() -> Result<u16, AppError> {
    let listener = std::net::TcpListener::bind("127.0.0.1:0")
        .map_err(|e| AppError::Process(format!("空きポート確保に失敗: {e}")))?;
    let port = listener
        .local_addr()
        .map_err(|e| AppError::Process(format!("確保ポート取得に失敗: {e}")))?
        .port();
    drop(listener);
    Ok(port)
}

pub fn generate_backend_auth_token() -> String {
    OsRng
        .sample_iter(&Alphanumeric)
        .take(43)
        .map(char::from)
        .collect()
}

const BACKEND_HEALTH_TIMEOUT: Duration = Duration::from_secs(120);
const BACKEND_HEALTH_INTERVAL: Duration = Duration::from_millis(100);
const BACKEND_HEALTH_CONNECT_TIMEOUT: Duration = Duration::from_millis(200);

pub fn is_ready_health_response(response: &str) -> bool {
    let status_ok = response.starts_with("HTTP/1.1 200 ") || response.starts_with("HTTP/1.0 200 ");
    let body_ok = response.contains("\"status\":\"ok\"") || response.contains("\"status\": \"ok\"");
    status_ok && body_ok
}

pub fn backend_health_timeout_message(port: u16, timeout: Duration) -> String {
    format!(
        "FastAPI /health did not become ready on port {port} within {}s",
        timeout.as_secs()
    )
}
fn probe_backend_health(port: u16, auth_token: &str) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, BACKEND_HEALTH_CONNECT_TIMEOUT) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(BACKEND_HEALTH_CONNECT_TIMEOUT));
    let _ = stream.set_write_timeout(Some(BACKEND_HEALTH_CONNECT_TIMEOUT));

    let request = format!(
        "GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {auth_token}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }
    is_ready_health_response(&response)
}

fn wait_for_backend_health(
    child: &mut (dyn ChildWrapper + Send),
    port: u16,
    auth_token: &str,
    timeout: Duration,
) -> Result<(), AppError> {
    let deadline = Instant::now() + timeout;

    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let (_code, _signal, description) = describe_exit_status(status);
                return Err(AppError::Process(format!(
                    "FastAPI exited before /health became ready on port {port}: {description}"
                )));
            }
            Ok(None) => {}
            Err(e) => {
                return Err(AppError::Process(format!(
                    "FastAPI readiness check failed before /health on port {port}: {e}"
                )));
            }
        }

        if probe_backend_health(port, auth_token) {
            return Ok(());
        }

        if Instant::now() >= deadline {
            return Err(AppError::Process(backend_health_timeout_message(
                port, timeout,
            )));
        }
        std::thread::sleep(BACKEND_HEALTH_INTERVAL);
    }
}

/// AppImage launchers may inject `PYTHONHOME` and `PYTHONPATH` for their own
/// runtime. Those paths must not leak into the separately managed uv environment.
fn clear_inherited_python_runtime(command: &mut Command) {
    command.env_remove("PYTHONHOME").env_remove("PYTHONPATH");
}

/// FastAPI サーバーを起動する。
/// 失敗時は最大 3 回別ポートでリトライする。
/// Unix: ProcessGroup::leader() でプロセスグループを作成し、kill 時に孫まで一括終了。
/// Windows: JobObject で同様のプロセスツリー kill を実現。
pub fn start_backend(app: &AppHandle) -> Result<BackendProcess, AppError> {
    let paths = resolve_runtime_paths(app)?;
    let app_data_dir = app.path().app_data_dir()?;
    let managed_api_base_url = crate::managed_auth::managed_api_base_url()
        .ok()
        .map(|url| url.as_str().trim_end_matches('/').to_owned());
    println!("[backend] app_data_dir: {}", app_data_dir.display());

    const MAX_RETRIES: u32 = 3;

    for attempt in 1..=MAX_RETRIES {
        let port = allocate_backend_port()?;
        let port_arg = port.to_string();
        let auth_token = generate_backend_auth_token();
        let managed_session_capability = generate_backend_auth_token();
        println!("[backend] selected port: {port} (attempt {attempt}/{MAX_RETRIES})");

        // CODEX_HOME is intentionally neither set nor removed during the development proof.
        // Codex inherits its existing official CLI login without copying auth.json or passing
        // any token/key through this boundary.
        let mut wrap = CommandWrap::with_new(&paths.uv, |cmd| {
            cmd.env("UV_PROJECT_ENVIRONMENT", &paths.venv_dir)
                .env_remove("VIRTUAL_ENV");
            clear_inherited_python_runtime(cmd);
            cmd
                // Ignore an untrusted parent override. Only the canonical result from the
                // platform-aware trusted installer/PATH discovery may cross this boundary.
                .env_remove("CODEX_BINARY")
                .env_remove("MANAGED_API_BASE_URL")
                .env("APP_DATA_DIR", &app_data_dir)
                .env("BACKEND_AUTH_TOKEN", &auth_token)
                .env("MANAGED_SESSION_CAPABILITY", &managed_session_capability)
                .env("MEETING_SUPPORTER_CODEX_WORK_ROOT", &paths.codex_work_root);

            if let Some(codex_binary) = &paths.codex_binary {
                cmd.env("CODEX_BINARY", codex_binary);
            }
            if let Some(managed_api_base_url) = &managed_api_base_url {
                cmd.env("MANAGED_API_BASE_URL", managed_api_base_url);
            }

            // DEBUG=1 is set automatically for debug builds.
            // PYTHONASYNCIODEBUG is deliberately not set here;
            // enable it explicitly when debugging asyncio issues.
            #[cfg(debug_assertions)]
            {
                cmd.env("DEBUG", "1");
            }

            cmd.args([
                "run",
                "--no-sync",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                &port_arg,
                "--no-access-log",
            ])
            .current_dir(&paths.python_dir);
        });

        #[cfg(unix)]
        wrap.wrap(ProcessGroup::leader());
        #[cfg(windows)]
        wrap.wrap(JobObject);

        match wrap.spawn() {
            Ok(mut child) => {
                println!("[backend] FastAPI started (pid={})", child.id());
                match wait_for_backend_health(
                    child.as_mut(),
                    port,
                    &auth_token,
                    BACKEND_HEALTH_TIMEOUT,
                ) {
                    Ok(()) => {
                        println!("[backend] FastAPI health check passed on port {port}");
                        return Ok(BackendProcess {
                            child: Some(child),
                            port: Some(port),
                            auth_token: Some(auth_token),
                            managed_session_capability: Some(managed_session_capability),
                            intentional_shutdown: false,
                            crash_info: None,
                        });
                    }
                    Err(e) if attempt < MAX_RETRIES => {
                        eprintln!("[backend] start attempt {attempt} health check failed: {e}, retrying...");
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                    Err(e) => {
                        let _ = child.kill();
                        let _ = child.wait();
                        return Err(e);
                    }
                }
            }
            Err(e) if attempt < MAX_RETRIES => {
                eprintln!("[backend] start attempt {attempt} failed: {e}, retrying...");
            }
            Err(e) => {
                return Err(AppError::Process(format!(
                    "FastAPI 起動失敗 (retried {MAX_RETRIES} times): {e}"
                )));
            }
        }
    }

    // Although logically unreachable with MAX_RETRIES = 3, Rust's
    // reachability analysis for `for` loops cannot guarantee all paths return,
    // so a fallback is needed for type-check compatibility.
    Err(AppError::Process(
        "FastAPI 起動に失敗しました (リトライ上限到達)".into(),
    ))
}

pub fn kill_backend(app: &AppHandle) -> Result<(), AppError> {
    let state = app.state::<BackendState>();
    let mut backend = state
        .lock()
        .map_err(|e| AppError::MutexPoison(e.to_string()))?;
    // 意図的なシャットダウンであることをマークし、refresh_backend_state が
    // クラッシュ情報を作成しないようにする。
    backend.intentional_shutdown = true;
    if let Some(mut child) = backend.child.take() {
        eprintln!("[backend] killing process tree...");
        let _ = child.kill();
        let _ = child.wait();
    }
    backend.port = None;
    backend.auth_token = None;
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::ExitStatus;

    #[cfg(unix)]
    fn make_exit_status_from_signal(sig: i32) -> ExitStatus {
        use std::os::unix::process::ExitStatusExt;
        // Reinterpret the signal number as a raw wait status.
        // In Unix wait status encoding, a non-zero value in the low 7 bits
        // indicates termination by signal, so from_raw(sig) makes
        // status.signal() return Some(sig) and status.code() return None.
        ExitStatus::from_raw(sig)
    }

    #[cfg(unix)]
    fn make_exit_status_from_code(code: i32) -> ExitStatus {
        // Unix: ExitStatus::from_raw で code を直接設定するのはプラットフォーム依存。
        // std::process::Command で実際のプロセスを終了させるのが安全。
        use std::os::unix::process::ExitStatusExt;
        // WIFEXITED | (code << 8) が POSIX の wait status フォーマット
        ExitStatus::from_raw((code & 0xff) << 8)
    }

    #[cfg(windows)]
    fn make_exit_status_from_code(code: i32) -> ExitStatus {
        use std::os::windows::process::ExitStatusExt;
        ExitStatus::from_raw(code as u32)
    }

    #[test]
    fn describe_exit_status_with_exit_code() {
        let status = make_exit_status_from_code(1);
        let (code, signal, desc) = describe_exit_status(status);
        assert_eq!(code, Some(1));
        assert_eq!(signal, None);
        assert!(desc.contains("code 1"), "desc was: {desc}");
    }

    #[test]
    fn describe_exit_status_with_zero_code() {
        let status = make_exit_status_from_code(0);
        let (code, _signal, desc) = describe_exit_status(status);
        assert_eq!(code, Some(0));
        assert!(desc.contains("code 0"), "desc was: {desc}");
    }

    #[cfg(unix)]
    #[test]
    fn describe_exit_status_with_signal() {
        let status = make_exit_status_from_signal(9); // SIGKILL
        let (code, signal, desc) = describe_exit_status(status);
        assert_eq!(code, None);
        assert_eq!(signal, Some(9));
        assert!(desc.contains("signal 9"), "desc was: {desc}");
        assert!(desc.contains("SIGKILL"), "desc was: {desc}");
    }

    #[cfg(unix)]
    #[test]
    fn signal_name_known_signals() {
        assert_eq!(signal_name(9), "SIGKILL");
        assert_eq!(signal_name(15), "SIGTERM");
        assert_eq!(signal_name(6), "SIGABRT");
        assert_eq!(signal_name(11), "SIGSEGV");
        assert_eq!(signal_name(255), "unknown signal");
    }

    #[test]
    fn build_crash_info_sets_unexpected_flag() {
        let status = make_exit_status_from_code(137);
        let crash = build_crash_info(status);
        assert!(crash.unexpected);
        assert_eq!(crash.exit_code, Some(137));
        assert!(
            crash.message.contains("unexpectedly"),
            "msg was: {}",
            crash.message
        );
    }

    #[cfg(unix)]
    #[test]
    fn build_crash_info_from_signal() {
        let status = make_exit_status_from_signal(11); // SIGSEGV
        let crash = build_crash_info(status);
        assert!(crash.unexpected);
        assert_eq!(crash.exit_code, None);
        assert_eq!(crash.signal, Some(11));
        assert!(
            crash.message.contains("SIGSEGV"),
            "msg was: {}",
            crash.message
        );
    }

    #[test]
    fn is_ready_health_response_accepts_http_200_ok_body() {
        let response = concat!(
            "HTTP/1.1 200 OK\r\n",
            "Content-Type: application/json\r\n",
            "\r\n",
            "{\"status\":\"ok\"}"
        );

        assert!(is_ready_health_response(response));
    }

    #[test]
    fn is_ready_health_response_rejects_non_200_or_non_ok_body() {
        let cases = [
            (
                "service unavailable status is not ready",
                "HTTP/1.1 503 Service Unavailable\r\n\r\n{\"status\":\"ok\"}",
            ),
            (
                "successful response with non-ok status is not ready",
                "HTTP/1.1 200 OK\r\n\r\n{\"status\":\"starting\"}",
            ),
        ];

        for (name, response) in cases {
            assert!(
                !is_ready_health_response(response),
                "{name} should be rejected"
            );
        }
    }

    #[test]
    fn generate_backend_auth_token_is_url_protocol_safe() {
        let token = generate_backend_auth_token();
        assert_eq!(token.len(), 43);
        assert!(
            token.chars().all(|ch| ch.is_ascii_alphanumeric()),
            "token should contain only WebSocket subprotocol-safe characters: {token}"
        );
    }

    #[test]
    fn backend_health_timeout_message_identifies_endpoint_and_port() {
        let message = backend_health_timeout_message(49152, Duration::from_secs(5));

        assert!(
            message.contains("/health"),
            "message should identify endpoint: {message}"
        );
        assert!(
            message.contains("49152"),
            "message should identify port: {message}"
        );
    }

    #[test]
    fn backend_command_clears_inherited_python_runtime() {
        let mut command = Command::new("uv");
        command
            .env("PYTHONHOME", "/appimage/runtime")
            .env("PYTHONPATH", "/appimage/runtime/lib");

        clear_inherited_python_runtime(&mut command);

        for key in ["PYTHONHOME", "PYTHONPATH"] {
            let override_value = command
                .get_envs()
                .find(|(name, _)| *name == std::ffi::OsStr::new(key))
                .map(|(_, value)| value);
            assert_eq!(
                override_value,
                Some(None),
                "{key} must be removed from the backend environment"
            );
        }
    }

    #[test]
    fn backend_process_none_has_no_crash_info() {
        let bp = BackendProcess::none();
        assert!(bp.crash_info.is_none());
        assert!(!bp.intentional_shutdown);
        assert!(bp.child.is_none());
        assert!(bp.port.is_none());
        assert!(bp.auth_token.is_none());
    }
}
