use meeting_desktop_runtime::{dto::http::*, openapi};
use poem_openapi::types::{ParseFromJSON, ToJSON};
use serde_json::{json, Value};

#[test]
fn settings_patch_preserves_omission_null_and_value_across_the_boundary() {
    for value in [
        json!({}),
        json!({"context": {"dir_override": null}, "recording_retention": {"cutoff_date": null}}),
        json!({"reply": {"auto_generate": false}, "usage_budget": {"monthly_limit_jpy": 0.0}}),
    ] {
        let dto = SettingsSaveRequest::parse_from_json(Some(value.clone()))
            .unwrap_or_else(|_| panic!("valid synthetic patch rejected"));
        assert_eq!(dto.to_json().unwrap(), value);
        let serialized = serde_json::to_value(&dto).unwrap();
        assert_eq!(serialized, value);
        let _: meeting_desktop_runtime::settings::Patch =
            serde_json::from_value(serialized).unwrap();
    }
}

#[test]
fn settings_contract_rejects_unknown_fields_and_retired_settings() {
    for value in [
        json!({"unknown": true}),
        json!({"stt": {"unknown": 1}}),
        json!({"secrets": {"DEEPGRAM_API_KEY": "synthetic"}}),
        json!({"context": {"unknown": null}}),
    ] {
        assert!(SettingsSaveRequest::parse_from_json(Some(value)).is_err());
    }
}

#[test]
fn response_serializers_agree_on_nulls_and_wire_enum_names() {
    let asset = json!({
        "id":"synthetic", "role":"self", "format":"wav", "sample_rate":16000,
        "channels":1, "started_at":"2026-01-01T00:00:00Z", "ended_at":null, "size_bytes":null,
        "relative_path":"recordings/synthetic/self.wav", "meeting_id":"synthetic"
    });
    let dto: RecordingAssetItem = serde_json::from_value(asset).unwrap();
    let value = serde_json::to_value(&dto).unwrap();
    assert_eq!(dto.to_json().unwrap(), value);
    assert_eq!(value["role"], "self");
    assert!(value.get("relative_path").is_none());
    assert!(value.get("meeting_id").is_none());
    let capabilities = SpeechCapabilities { whisper_gpu: None };
    assert_eq!(capabilities.to_json().unwrap(), json!({"whisper_gpu":null}));
}

#[test]
fn exported_contract_covers_rust_only_routes_and_typed_settings() {
    let first = openapi();
    assert_eq!(first, openapi());
    let spec: Value = serde_json::from_str(&first).unwrap();
    assert_eq!(spec["info"]["version"], env!("CARGO_PKG_VERSION"));
    for path in ["/api/ai/agents", "/api/stt/capabilities", "/meetings"] {
        let content = spec["paths"][path]["get"]["responses"]["200"]["content"]
            .as_object()
            .unwrap();
        assert!(content.iter().any(
            |(media, body)| media.starts_with("application/json") && body["schema"].is_object()
        ));
        assert!(!spec["paths"][path]["get"]["security"]
            .as_array()
            .unwrap()
            .is_empty());
    }
    assert_eq!(
        spec["paths"]["/api/settings"]["get"]["operationId"],
        "get_settings_api_settings_get"
    );
    assert_eq!(
        spec["components"]["schemas"]["SettingsResponse"]["properties"]["stt"]["$ref"],
        "#/components/schemas/SttSettings"
    );
    assert!(
        spec["components"]["schemas"]["SettingsSaveRequest"]["properties"]["context"].is_object()
    );
}
