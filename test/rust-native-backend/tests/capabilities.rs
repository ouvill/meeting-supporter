#[test]
fn reports_build_support_without_models_or_runtime_arguments() {
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_meeting-native-backend"))
        .arg("--capabilities")
        .output()
        .unwrap();
    assert!(output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["protocol"], 1);
    assert_eq!(
        value["whisper_gpu"],
        cfg!(all(
            feature = "whisper",
            any(feature = "vulkan", feature = "cuda", feature = "metal")
        ))
    );
    assert!(output.stderr.is_empty());
}
