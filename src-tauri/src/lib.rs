pub mod commands;
pub mod desktop_runtime;
mod desktop_secrets;
pub mod error;
pub mod managed_auth;
pub mod native_speech;
pub mod state;

use tauri::{Manager, WindowEvent};
use tauri_plugin_deep_link::DeepLinkExt;

use crate::state::{set_bootstrap_status, BootstrapState, BootstrapStateData};
#[derive(Debug, PartialEq, Eq)]
enum NativeClosePolicy {
    ExitApplication,
    HideWindow,
    CloseWindow,
}

fn native_close_policy(label: &str) -> NativeClosePolicy {
    match label {
        "main" => NativeClosePolicy::ExitApplication,
        "assistant" => NativeClosePolicy::HideWindow,
        _ => NativeClosePolicy::CloseWindow,
    }
}

fn request_exit(app: &tauri::AppHandle) {
    native_speech::shutdown(app);
    desktop_runtime::request_exit(app);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|_app, _argv, _cwd| {}))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init());

    #[cfg(all(debug_assertions, feature = "webdriver"))]
    let builder = if std::env::var_os("TAURI_WEBDRIVER_PORT").is_some() {
        builder
            .plugin(tauri_plugin_wdio::init())
            .plugin(tauri_plugin_wdio_webdriver::init())
    } else {
        builder
    };

    builder
        .manage(desktop_runtime::DesktopState::default())
        .manage(BootstrapState::new(BootstrapStateData::new()))
        .manage(managed_auth::ManagedAuthState::default())
        .manage(native_speech::NativeSpeechState::default())
        .setup(|app| {
            #[cfg(any(target_os = "windows", target_os = "linux"))]
            if std::env::var_os("TAURI_WEBDRIVER_PORT").is_none() {
                app.deep_link().register_all()?;
            }
            let deep_link_app = app.handle().clone();
            app.deep_link().on_open_url(move |event| {
                for url in event.urls() {
                    managed_auth::handle_deep_link(deep_link_app.clone(), url.clone());
                }
            });
            if let Some(urls) = app.deep_link().get_current()? {
                for url in urls {
                    managed_auth::handle_deep_link(app.handle().clone(), url);
                }
            }

            let app_handle_for_signal = app.handle().clone();
            ctrlc::set_handler(move || request_exit(&app_handle_for_signal))
                .expect("ctrlc ハンドラ設定失敗");

            if cfg!(feature = "native-speech") {
                set_bootstrap_status(app.handle(), "running", "Local speech controls are ready.")?;
            } else {
                set_bootstrap_status(app.handle(), "starting", "Starting Rust backend...")?;
                desktop_runtime::start(app.handle().clone());
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            let WindowEvent::CloseRequested { api, .. } = event else {
                return;
            };
            match native_close_policy(window.label()) {
                NativeClosePolicy::ExitApplication => {
                    api.prevent_close();
                    request_exit(window.app_handle());
                }
                NativeClosePolicy::HideWindow => {
                    api.prevent_close();
                    if let Err(error) = window.hide() {
                        eprintln!("[window] failed to hide assistant window: {error}");
                    }
                }
                NativeClosePolicy::CloseWindow => {}
            }
        })
        .invoke_handler(tauri::generate_handler![
            native_speech::get_runtime_mode,
            native_speech::native_speech_defaults,
            native_speech::native_speech_snapshot,
            native_speech::native_speech_start,
            native_speech::native_speech_stop,
            native_speech::native_speech_clear,
            native_speech::native_speech_export,
            commands::get_api_port,
            commands::get_api_auth_token,
            commands::is_backend_running,
            commands::get_backend_bootstrap_status,
            commands::set_assistant_window_visible,
            managed_auth::managed_auth_start,
            managed_auth::managed_auth_status,
            managed_auth::managed_auth_logout,
            managed_auth::managed_entitlement,
            managed_auth::managed_checkout,
            managed_auth::managed_billing_portal,
            managed_auth::managed_delete_account,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let tauri::RunEvent::ExitRequested { api, .. } = event {
                if !desktop_runtime::exit_ready(app_handle) {
                    api.prevent_exit();
                    request_exit(app_handle);
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::{native_close_policy, NativeClosePolicy};
    use serde_json::Value;

    fn window_config<'a>(config: &'a Value, label: &str) -> &'a Value {
        config["app"]["windows"]
            .as_array()
            .expect("app.windows must be an array")
            .iter()
            .find(|window| window["label"] == label)
            .unwrap_or_else(|| panic!("missing {label} window configuration"))
    }

    #[test]
    fn native_window_configuration_matches_product_policy() {
        let config: Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).expect("valid Tauri config");
        let main = window_config(&config, "main");
        let assistant = window_config(&config, "assistant");

        assert_eq!(main["title"], "会議支援AI");
        assert_eq!(main["decorations"], true);
        assert_eq!(main["alwaysOnTop"], false);
        assert_eq!(
            native_close_policy("main"),
            NativeClosePolicy::ExitApplication
        );

        assert_eq!(assistant["title"], "ライブ返答支援");
        assert_eq!(assistant["decorations"], true);
        assert_eq!(assistant["alwaysOnTop"], true);
        assert_eq!(
            native_close_policy("assistant"),
            NativeClosePolicy::HideWindow
        );
    }
}
