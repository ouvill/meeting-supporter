mod uv;

use std::path::PathBuf;
use tauri::{AppHandle, Manager};

use crate::error::AppError;

pub use uv::{compute_backoff_delay, ensure_python_environment, find_uv, managed_uv_path};

pub struct RuntimePaths {
    pub uv: PathBuf,
    pub python_dir: PathBuf,
    pub venv_dir: PathBuf,
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

    Ok(RuntimePaths {
        uv,
        python_dir,
        venv_dir,
    })
}

#[cfg(test)]
mod tests;
