use super::*;
use std::ffi::OsStr;
use std::path::Path;
use std::time::Duration;

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
