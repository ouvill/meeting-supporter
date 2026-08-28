mod codex;
mod uv;

use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager};

use crate::error::AppError;

pub use codex::find_codex;
pub use uv::{compute_backoff_delay, ensure_python_environment, find_uv, managed_uv_path};

const CODEX_WORK_ROOT_DIR: &str = "codex-work";

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
        uv::download_uv(&uv)?;
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

#[cfg(test)]
mod tests;
