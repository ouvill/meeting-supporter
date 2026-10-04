#![cfg(feature = "test-fixtures")]
use futures_util::{SinkExt, StreamExt};
use meeting_desktop_runtime::{Config, Server};
use serde_json::{json, Value};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpStream,
};
use tokio_tungstenite::{
    tungstenite::{client::IntoClientRequest, Message},
    MaybeTlsStream, WebSocketStream,
};

type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;
fn config(temp: &tempfile::TempDir, model: &str) -> Config {
    let model = temp.path().join(model);
    std::fs::create_dir_all(&model).unwrap();
    for name in [
        "tokens.txt",
        "encoder-epoch-99-avg-1.int8.onnx",
        "decoder-epoch-99-avg-1.int8.onnx",
        "joiner-epoch-99-avg-1.int8.onnx",
    ] {
        std::fs::write(model.join(name), "synthetic model marker").unwrap();
    }
    Config {
        agent_updates: false,
        data_dir: temp.path().join("data"),
        audio_worker: env!("CARGO_BIN_EXE_synthetic-worker").into(),
        speech_worker: env!("CARGO_BIN_EXE_synthetic-worker").into(),
        model: Some(model),
        legacy_model: None,
        hub_cache: temp.path().join("hub"),
        hub_offline: true,
        punctuation: None,
        python_worker: env!("CARGO_BIN_EXE_synthetic-worker").into(),
    }
}
async fn connect(server: &Server) -> Socket {
    let mut request = format!("ws://127.0.0.1:{}/ws", server.port)
        .into_client_request()
        .unwrap();
    request.headers_mut().insert(
        "Sec-WebSocket-Protocol",
        format!("auth.{}", server.token).parse().unwrap(),
    );
    tokio_tungstenite::connect_async(request).await.unwrap().0
}
async fn send(ws: &mut Socket, message: Value) {
    ws.send(Message::Text(message.to_string().into()))
        .await
        .unwrap();
}
async fn until(ws: &mut Socket, predicate: impl Fn(&Value) -> bool) -> Value {
    tokio::time::timeout(std::time::Duration::from_secs(15), async {
        loop {
            let msg = ws.next().await.unwrap().unwrap();
            if let Message::Text(text) = msg {
                let v: Value = serde_json::from_str(&text).unwrap();
                if predicate(&v) {
                    return v;
                }
            }
        }
    })
    .await
    .unwrap()
}
async fn http(server: &Server, method: &str, path: &str, extra: &str) -> (u16, Vec<u8>) {
    let mut stream = TcpStream::connect(("127.0.0.1", server.port))
        .await
        .unwrap();
    stream.write_all(format!("{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {}\r\nConnection: close\r\n{extra}\r\n",server.token).as_bytes()).await.unwrap();
    let mut bytes = Vec::new();
    stream.read_to_end(&mut bytes).await.unwrap();
    let split = bytes.windows(4).position(|w| w == b"\r\n\r\n").unwrap();
    let status = std::str::from_utf8(&bytes[..split])
        .unwrap()
        .split_whitespace()
        .nth(1)
        .unwrap()
        .parse()
        .unwrap();
    (status, bytes[split + 4..].to_vec())
}
async fn delete_when_idle(server: &Server, id: &str) {
    // The final state is broadcast before the command's runtime guard is
    // released. DELETE deliberately reports 409 during that short interval.
    tokio::time::timeout(std::time::Duration::from_secs(5), async {
        loop {
            let status = http(server, "DELETE", &format!("/meetings/{id}"), "")
                .await
                .0;
            if status == 200 {
                break;
            }
            assert_eq!(status, 409);
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
}
async fn start(ws: &mut Socket) {
    until(ws, |v| v["type"] == "devices_list").await;
    send(ws, json!({"type":"init_stt"})).await;
    until(ws, |v| v["type"] == "stt_state" && v["initialized"] == true).await;
    send(ws,json!({"type":"start_meeting","meeting_context":{"scenario":"synthetic","objective":"test"},"references":[]})).await;
    until(ws, |v| v["type"] == "meeting_state" && v["running"] == true).await;
    wait_for_both_audio_inputs(ws).await;
}

async fn wait_for_both_audio_inputs(ws: &mut Socket) {
    // Observe capture progress after StartSpeech attached both routes. A fixed
    // sleep can expire before either worker is scheduled on a loaded runner.
    for role in ["self", "other"] {
        until(ws, |v| {
            v["type"] == "audio_level"
                && v["role"] == role
                && v["level"].as_f64().is_some_and(|p| p > 0.0)
        })
        .await;
    }
}

#[tokio::test]
async fn speech_capabilities_come_from_the_configured_worker_without_preparing_models() {
    for (name, expected) in [
        ("cpu-worker", json!(false)),
        ("gpu-worker", json!(true)),
        ("invalid-worker", Value::Null),
        ("old-worker", Value::Null),
        ("failed-worker", Value::Null),
        ("missing-worker", Value::Null),
        ("hanging-worker", Value::Null),
    ] {
        let temp = tempfile::tempdir().unwrap();
        let mut settings = config(&temp, "model");
        let worker = temp
            .path()
            .join(format!("{name}{}", std::env::consts::EXE_SUFFIX));
        if name != "missing-worker" {
            std::fs::copy(&settings.speech_worker, &worker).unwrap();
        }
        settings.speech_worker = worker;
        let server = Server::start(settings.clone()).await.unwrap();
        let (status, body) = tokio::time::timeout(
            std::time::Duration::from_secs(10),
            http(&server, "GET", "/api/stt/capabilities", ""),
        )
        .await
        .unwrap();
        assert_eq!(status, 200, "{name}");
        let value: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(value, json!({"whisper_gpu":expected}), "{name}");
        assert!(!settings.model.unwrap().join("preparing").exists());
        server.shutdown().await.unwrap();
    }
}

#[tokio::test]
async fn existing_ui_flow_drains_both_final_transcripts_and_serves_recording_ranges() {
    let temp = tempfile::tempdir().unwrap();
    let server = Server::start(config(&temp, "model")).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    send(
        &mut ws,
        json!({"type":"manual_speech","text":"synthetic manual"}),
    )
    .await;
    until(&mut ws, |v| {
        v["type"] == "stt_final" && v["text"] == "synthetic manual"
    })
    .await;
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    let stopped = until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    assert_eq!(stopped["saved"], true);
    let (status, body) = http(&server, "GET", "/meetings", "").await;
    assert_eq!(status, 200);
    let page: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(page["total"], 1);
    let id = page["items"][0]["id"].as_str().unwrap();
    assert_eq!(page["items"][0]["status"], "completed");
    assert_eq!(page["items"][0]["has_recording"], true);
    let (_, body) = http(&server, "GET", &format!("/meetings/{id}"), "").await;
    let detail: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(detail["turns"].as_array().unwrap().len(), 3);
    assert_eq!(detail["recording_assets"].as_array().unwrap().len(), 2);
    assert!(detail["recording_assets"][0].get("relative_path").is_none());
    assert!(detail["recording_assets"][0].get("meeting_id").is_none());
    let (status, body) = http(
        &server,
        "GET",
        &format!("/meetings/{id}/recordings/self"),
        "Range: bytes=0-43\r\n",
    )
    .await;
    assert_eq!(status, 206);
    assert_eq!(body.len(), 44);
    assert_eq!(&body[..4], b"RIFF");
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let recording_url = format!(
        "http://127.0.0.1:{}/meetings/{id}/recordings/self",
        server.port
    );
    let full = client
        .get(&recording_url)
        .bearer_auth(&server.token)
        .send()
        .await
        .unwrap();
    assert_eq!(full.status(), 200);
    assert_eq!(full.headers()["content-type"], "audio/wav");
    let etag = full.headers()["etag"].clone();
    let cached = client
        .get(&recording_url)
        .bearer_auth(&server.token)
        .header("If-None-Match", etag)
        .send()
        .await
        .unwrap();
    assert_eq!(cached.status(), 304);
    let invalid = client
        .get(&recording_url)
        .bearer_auth(&server.token)
        .header("Range", "bytes=999999999-")
        .send()
        .await
        .unwrap();
    assert_eq!(invalid.status(), 416);
    assert!(invalid.headers()["content-range"]
        .to_str()
        .unwrap()
        .starts_with("bytes */"));
    let (status, _) = http(&server, "DELETE", &format!("/meetings/{id}"), "").await;
    assert_eq!(status, 200);
    assert!(!temp.path().join("data/recordings").join(id).exists());
    drop(ws);
    server.shutdown().await.unwrap();
}
#[tokio::test]
async fn inference_crash_preserves_records_and_allows_another_meeting() {
    let temp = tempfile::tempdir().unwrap();
    let server = Server::start(config(&temp, "crash")).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    let stopped = until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    assert_eq!(stopped["saved"], false);
    assert_eq!(stopped["end_status"], "interrupted");
    let (_, body) = http(&server, "GET", "/meetings", "").await;
    let page: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(page["items"][0]["status"], "aborted");
    assert_eq!(page["items"][0]["has_recording"], true);
    let id = page["items"][0]["id"].as_str().unwrap();
    delete_when_idle(&server, &id).await;
    send(&mut ws, json!({"type":"init_stt"})).await;
    until(&mut ws, |v| {
        v["type"] == "stt_state" && v["initialized"] == true
    })
    .await;
    send(&mut ws, json!({"type":"start_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == true
    })
    .await;
    // Current ownership, rather than the status of an older meeting, protects deletion.
    let (_, body) = http(&server, "GET", "/meetings", "").await;
    let page: Value = serde_json::from_slice(&body).unwrap();
    let active_id = page["items"][0]["id"].as_str().unwrap();
    assert_eq!(
        http(&server, "DELETE", &format!("/meetings/{active_id}"), "")
            .await
            .0,
        409
    );
    drop(ws);
    server.shutdown().await.unwrap();
}
#[tokio::test]
async fn app_shutdown_finalizes_active_meeting() {
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "model");
    let server = Server::start(settings.clone()).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    server.shutdown().await.unwrap();
    drop(ws);
    let repo =
        meeting_storage::Repository::open(&settings.data_dir.join("meeting_history.sqlite3"))
            .await
            .unwrap();
    let rows = repo
        .execute(meeting_storage::models::Command::ListMeetings {
            limit: 10,
            offset: 0,
        })
        .await
        .unwrap();
    assert_eq!(rows[0]["status"], "completed");
    let turns = repo
        .execute(meeting_storage::models::Command::ListTurns {
            meeting_id: rows[0]["id"].as_str().unwrap().into(),
        })
        .await
        .unwrap();
    assert_eq!(turns.as_array().unwrap().len(), 2);
    repo.close().await;
}
#[tokio::test]
async fn shutdown_interrupts_preparation_and_reaps_inference() {
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "hang");
    let server = Server::start(settings.clone()).await.unwrap();
    let mut ws = connect(&server).await;
    until(&mut ws, |v| v["type"] == "devices_list").await;
    send(&mut ws, json!({"type":"init_stt"})).await;
    tokio::time::timeout(std::time::Duration::from_secs(5), async {
        while !settings.model.as_ref().unwrap().join("preparing").exists() {
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    tokio::time::timeout(std::time::Duration::from_secs(5), server.shutdown())
        .await
        .unwrap()
        .unwrap();
    drop(ws);
    #[cfg(target_os = "linux")]
    for item in std::fs::read_dir(settings.model.as_ref().unwrap())
        .unwrap()
        .flatten()
    {
        if let Some(pid) = item.file_name().to_str().unwrap().strip_prefix("pid-") {
            assert!(!std::path::Path::new("/proc").join(pid).exists());
        }
    }
}
#[tokio::test]
async fn rejects_untrusted_origins_and_missing_auth() {
    let temp = tempfile::tempdir().unwrap();
    let server = Server::start(config(&temp, "model")).await.unwrap();
    assert_eq!(
        http(
            &server,
            "GET",
            "/meetings",
            "Origin: https://example.invalid\r\n"
        )
        .await
        .0,
        403
    );
    let url = format!("ws://127.0.0.1:{}/ws", server.port);
    assert!(tokio_tungstenite::connect_async(url).await.is_err());
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn poem_boundary_preserves_auth_cors_patch_semantics_and_safe_errors() {
    let temp = tempfile::tempdir().unwrap();
    let server = Server::start(config(&temp, "model")).await.unwrap();
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let base = format!("http://127.0.0.1:{}", server.port);
    let url = format!("{base}/api/settings");
    assert_eq!(client.get(&url).send().await.unwrap().status(), 401);
    let preflight = client
        .request(reqwest::Method::OPTIONS, &url)
        .header("Origin", "http://localhost:1420")
        .header("Access-Control-Request-Method", "POST")
        .header(
            "Access-Control-Request-Headers",
            "authorization,content-type",
        )
        .send()
        .await
        .unwrap();
    assert!(preflight.status().is_success());
    assert_eq!(
        preflight.headers()["access-control-allow-origin"],
        "http://localhost:1420"
    );

    let send_patch = |value: Value| {
        client
            .post(&url)
            .bearer_auth(&server.token)
            .json(&value)
            .send()
    };
    let set = send_patch(
        json!({"recording_retention":{"cutoff_date":"2026-01-01","max_total_bytes":1024}}),
    )
    .await
    .unwrap();
    assert_eq!(set.status(), 200);
    let kept: Value = send_patch(json!({"reply":{"auto_generate":true}}))
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(
        kept["settings"]["recording_retention"]["max_total_bytes"],
        1024
    );
    let cleared: Value = send_patch(json!({"recording_retention":{"max_total_bytes":null}}))
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert!(cleared["settings"]["recording_retention"]["max_total_bytes"].is_null());
    assert_eq!(
        cleared["settings"]["recording_retention"]["cutoff_date"],
        "2026-01-01"
    );
    for bad in [
        json!({"stt":{"backend":"cloud"}}),
        json!({"stt":{"vad_engine":"webrtc"}}),
        json!({"context":{"unexpected":true}}),
    ] {
        assert_eq!(send_patch(bad).await.unwrap().status(), 422);
    }
    let malformed = client
        .post(format!("{base}/api/settings/connections/test"))
        .bearer_auth(&server.token)
        .json(&json!({"provider":"synthetic-private-value", "api_key":"synthetic-private-value"}))
        .send()
        .await
        .unwrap();
    assert_eq!(malformed.status(), 422);
    assert!(!malformed
        .text()
        .await
        .unwrap()
        .contains("synthetic-private-value"));
    let chunks = futures_util::stream::iter([Ok::<_, std::io::Error>(
        r#"{"reply":{"auto_generate":false}}"#.to_owned(),
    )]);
    let chunked = client
        .post(&url)
        .bearer_auth(&server.token)
        .header("Content-Type", "application/json")
        .body(reqwest::Body::wrap_stream(chunks))
        .send()
        .await
        .unwrap();
    assert_eq!(chunked.status(), 200);
    let too_large = client
        .post(&url)
        .bearer_auth(&server.token)
        .header("Content-Type", "application/json")
        .body(" ".repeat(1024 * 1024 + 1))
        .send()
        .await
        .unwrap();
    assert_eq!(too_large.status(), 413);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn user_can_cancel_preparation_without_exiting_the_app() {
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "hang");
    let server = Server::start(settings.clone()).await.unwrap();
    let mut ws = connect(&server).await;
    until(&mut ws, |v| v["type"] == "devices_list").await;
    send(&mut ws, json!({"type":"init_stt"})).await;
    tokio::time::timeout(std::time::Duration::from_secs(5), async {
        while !settings.model.as_ref().unwrap().join("preparing").exists() {
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    send(&mut ws, json!({"type":"shutdown_stt"})).await;
    until(&mut ws, |v| {
        v["type"] == "stt_state" && v["initializing"] == false && v["initialized"] == false
    })
    .await;
    assert!(server.is_running());
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn missing_workers_do_not_prevent_history_or_shell_startup() {
    let temp = tempfile::tempdir().unwrap();
    let mut settings = config(&temp, "model");
    settings.audio_worker = temp.path().join("absent");
    let server = Server::start(settings).await.unwrap();
    let mut ws = connect(&server).await;
    let devices = until(&mut ws, |v| v["type"] == "devices_list").await;
    assert_eq!(devices["devices"], json!([]));
    send(&mut ws, json!({"type":"init_stt"})).await;
    until(&mut ws, |v| v["type"] == "error").await;
    assert_eq!(http(&server, "GET", "/meetings", "").await.0, 200);
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn reconnect_restores_history_and_duplicate_start_does_not_create_another_draft() {
    let temp = tempfile::tempdir().unwrap();
    let server = Server::start(config(&temp, "model")).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    send(
        &mut ws,
        json!({"type":"user_reply","text":"synthetic reconnect"}),
    )
    .await;
    until(&mut ws, |v| v["type"] == "stt_final").await;
    drop(ws);
    let mut ws = connect(&server).await;
    until(&mut ws, |v| v["type"] == "session_info").await;
    let snapshot = until(&mut ws, |v| v["type"] == "history_reset").await;
    assert_eq!(snapshot["items"][0]["text"], "synthetic reconnect");
    send(&mut ws, json!({"type":"start_meeting"})).await;
    until(&mut ws, |v| v["type"] == "error").await;
    let (_, body) = http(&server, "GET", "/meetings", "").await;
    let page: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(page["total"], 1);
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn failed_transcript_write_prevents_saved_notification_and_completion() {
    use meeting_storage::{
        models::{Command, Turn},
        Repository,
    };
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "model");
    let server = Server::start(settings.clone()).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    let repository = Repository::open(&settings.data_dir.join("meeting_history.sqlite3"))
        .await
        .unwrap();
    let rows = repository
        .execute(Command::ListMeetings {
            limit: 10,
            offset: 0,
        })
        .await
        .unwrap();
    let id = rows[0]["id"].as_str().unwrap().to_owned();
    // Inject a conflicting sequence in a temporary DB to fail the next transcript write.
    repository
        .execute(Command::InsertTurn {
            record: Turn {
                id: "synthetic-conflict".into(),
                meeting_id: id.clone(),
                sequence: 1,
                speaker: "self".into(),
                text: "synthetic constraint".into(),
                speaker_id: None,
                created_at: None,
            },
        })
        .await
        .unwrap();
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    let stopped = until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    assert_eq!(stopped["saved"], false);
    assert_eq!(stopped["end_status"], "unsaved");
    let record = repository
        .execute(Command::GetMeeting { meeting_id: id })
        .await
        .unwrap();
    assert_eq!(record["status"], "aborted");
    repository.close().await;
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn repeated_meetings_keep_transcripts_in_their_own_session() {
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "model");
    let model = settings.model.clone().unwrap();
    let server = Server::start(settings).await.unwrap();
    for generation in 1..=2 {
        let mut ws = connect(&server).await;
        start(&mut ws).await;
        // Both inference inputs must actually receive PCM before Stop. The
        // test checks session attribution, not how fast a worker is scheduled.
        tokio::time::timeout(std::time::Duration::from_secs(5), async {
            while !(0..2).all(|role| model.join(format!("audio-{role}-{generation}")).exists()) {
                tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();
        send(&mut ws, json!({"type":"stop_meeting"})).await;
        let stopped = until(&mut ws, |v| {
            v["type"] == "meeting_state" && v["running"] == false
        })
        .await;
        assert_eq!(stopped["saved"], true);
        drop(ws);
    }
    let (_, body) = http(&server, "GET", "/meetings", "").await;
    let rows: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(rows["total"], 2);
    for meeting in rows["items"].as_array().unwrap() {
        let (_, body) = http(
            &server,
            "GET",
            &format!("/meetings/{}", meeting["id"].as_str().unwrap()),
            "",
        )
        .await;
        let detail: Value = serde_json::from_slice(&body).unwrap();
        let turns = detail["turns"].as_array().unwrap();
        assert_eq!(turns.len(), 2);
        for (speaker, text) in [("self", "synthetic 0。"), ("other", "synthetic 1。")] {
            assert!(turns
                .iter()
                .any(|turn| turn["speaker"] == speaker && turn["text"] == text));
        }
    }
    assert_eq!(
        std::fs::read_dir(model)
            .unwrap()
            .flatten()
            .filter(|f| f.file_name().to_string_lossy().starts_with("pid-"))
            .count(),
        1,
        "ReazonSpeech serves both inputs and meetings with one model"
    );
    server.shutdown().await.unwrap();
}

async fn post_settings(server: &Server, value: Value) -> (u16, Value) {
    let body = value.to_string();
    let extra = format!(
        "Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
        body.len(),
        body
    );
    let (status, body) = http(server, "POST", "/api/settings", &extra).await;
    (status, serde_json::from_slice(&body).unwrap())
}

#[tokio::test]
async fn meters_run_before_model_preparation_after_device_changes_and_after_stop() {
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "model");
    let server = Server::start(settings.clone()).await.unwrap();
    let mut ws = connect(&server).await;
    until(&mut ws, |v| {
        v["type"] == "audio_level"
            && v["role"] == "self"
            && v["level"].as_f64().is_some_and(|l| l > 0.0)
    })
    .await;
    until(&mut ws, |v| {
        v["type"] == "audio_level"
            && v["role"] == "other"
            && v["level"].as_f64().is_some_and(|l| l > 0.0)
    })
    .await;
    assert_eq!(
        std::fs::read_dir(settings.model.as_ref().unwrap())
            .unwrap()
            .count(),
        4,
        "metering must not load models"
    );
    send(
        &mut ws,
        json!({"type":"set_device","role":"self","device":"mic"}),
    )
    .await;
    until(&mut ws, |v| {
        v["type"] == "devices_list" && v["current_self"] == "mic"
    })
    .await;
    until(&mut ws, |v| {
        v["type"] == "audio_level"
            && v["role"] == "self"
            && v["level"].as_f64().is_some_and(|l| l > 0.0)
    })
    .await;
    send(&mut ws, json!({"type":"init_stt"})).await;
    until(&mut ws, |v| {
        v["type"] == "stt_state" && v["initialized"] == true
    })
    .await;
    send(&mut ws, json!({"type":"start_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == true
    })
    .await;
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    until(&mut ws, |v| {
        v["type"] == "audio_level"
            && v["role"] == "self"
            && v["level"].as_f64().is_some_and(|l| l > 0.0)
    })
    .await;
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn unavailable_os_credentials_do_not_block_local_settings() {
    struct OfflineSecrets;
    impl meeting_desktop_runtime::settings::Secrets for OfflineSecrets {
        fn get(&self, _: &str) -> Result<Option<String>, meeting_desktop_runtime::Error> {
            Err(meeting_desktop_runtime::Error::Secrets)
        }
        fn set(&self, _: &str, _: Option<&str>) -> Result<(), meeting_desktop_runtime::Error> {
            Err(meeting_desktop_runtime::Error::Secrets)
        }
    }
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "model");
    let server = Server::start_with_secrets(settings.clone(), std::sync::Arc::new(OfflineSecrets))
        .await
        .unwrap();
    assert_eq!(http(&server, "GET", "/api/settings", "").await.0, 200);
    assert_eq!(
        post_settings(&server, json!({"stt":{"silence_duration":0.8}}))
            .await
            .0,
        200
    );
    let path = settings.data_dir.join("settings.toml");
    let saved = std::fs::read(&path).unwrap();
    let (_, body) = http(&server, "GET", "/api/ai/routes", "").await;
    let catalog: Value = serde_json::from_slice(&body).unwrap();
    for route in catalog["routes"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|route| route["kind"] == "byok")
    {
        assert_eq!(route["readiness"], "error");
        assert_eq!(route["reason_code"], "CREDENTIAL_STORE_UNAVAILABLE");
    }
    assert_eq!(
        post_settings(
            &server,
            json!({"secrets":{"OPENAI_API_KEY":"synthetic-new"}})
        )
        .await
        .0,
        500
    );
    assert_eq!(std::fs::read(path).unwrap(), saved);
    assert!(!settings.data_dir.join("secrets.toml").exists());
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn settings_survive_restart_and_reconfigure_speech() {
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "model");
    std::fs::create_dir_all(&settings.data_dir).unwrap();
    std::fs::write(settings.data_dir.join("settings.toml"),"schema_version = 1\n[ai.assignments]\nreply = 'gemini'\n[ai.routes.gemini]\nmodel = 'synthetic-model'\n[stt]\nvad_sensitivity = 0.6\n").unwrap();
    let server = Server::start(settings.clone()).await.unwrap();
    let (_, body) = http(&server, "GET", "/api/settings", "").await;
    let body: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(body["stt"]["vad_sensitivity"], 0.6);
    assert_eq!(body["ai_models"]["gemini"], "synthetic-model");
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    let (status, body) = post_settings(&server, json!({"stt":{"vad_sensitivity":0.7}})).await;
    assert_eq!(status, 409);
    assert_eq!(body["detail"]["code"], "AUDIO_SETTINGS_LOCKED");
    assert_eq!(
        post_settings(&server, json!({"usage_budget":{"meeting_limit_jpy":200}}))
            .await
            .0,
        200
    );
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    // The stop effect also opens capture-only monitors before releasing the control lock.
    until(&mut ws, |v| {
        v["type"] == "audio_level" && v["level"].as_f64().is_some_and(|l| l > 0.0)
    })
    .await;
    let (status,body)=post_settings(&server,json!({"ai_models":{"openai":"synthetic-openai","gemini":"synthetic-gemini","anthropic":"synthetic-anthropic","ollama":"synthetic-ollama"},"stt":{"vad_sensitivity":0.7,"silence_duration":0.9},"reply":{"styles":[{"id":"standard","enabled":false}],"enabled":false},"recording_retention":{"cutoff_date":null,"max_total_bytes":null}})).await;
    assert_eq!((status, body["ok"].clone()), (200, json!(true)));
    send(&mut ws, json!({"type":"init_stt"})).await;
    until(&mut ws, |v| {
        v["type"] == "stt_state" && v["initialized"] == true
    })
    .await;
    let configured: Vec<Value> = std::fs::read_dir(settings.model.as_ref().unwrap())
        .unwrap()
        .flatten()
        .filter(|p| p.file_name().to_string_lossy().starts_with("configured-"))
        .map(|p| serde_json::from_slice(&std::fs::read(p.path()).unwrap()).unwrap())
        .collect();
    assert!(configured.iter().any(|v| v["silence_seconds"] == 0.9
        && (v["vad_threshold"].as_f64().unwrap() - 0.7).abs() < 0.0001));
    drop(ws);
    server.shutdown().await.unwrap();
    let text = std::fs::read_to_string(settings.data_dir.join("settings.toml")).unwrap();
    let persisted: toml::Value = toml::from_str(&text).unwrap();
    assert_eq!(
        persisted["ai"]["assignments"]["reply"].as_str(),
        Some("gemini")
    );
    assert_eq!(persisted["schema_version"].as_integer(), Some(1));
    assert_eq!(
        persisted["ai"]["routes"]["gemini"]["model"].as_str(),
        Some("synthetic-gemini")
    );
    assert_eq!(
        persisted["ai"]["routes"]["openai"]["model"].as_str(),
        Some("synthetic-openai")
    );
    let server = Server::start(settings).await.unwrap();
    let (_, body) = http(&server, "GET", "/api/settings", "").await;
    let body: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(body["stt"]["silence_duration"], 0.9);
    assert_eq!(body["reply"]["enabled"], false);
    assert_eq!(body["ai_models"]["ollama"], "synthetic-ollama");
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn invalid_or_unsupported_settings_do_not_change_file_or_prepared_state() {
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "model");
    let server = Server::start(settings.clone()).await.unwrap();
    for patch in [
        json!({"stt":{"vad_sensitivity":9}}),
        json!({"stt":{"min_voiced_ms":true}}),
        json!({"stt":{"unknown":1}}),
        json!({"stt":{"no_speech_threshold":0.6}}),
        json!({"secrets":{"DEEPGRAM_API_KEY":"synthetic-unused"}}),
        json!({"stt":{"backend":"openai"}}),
        json!({"stt":{"backend":"whisper","device":"cuda"}}),
        json!({"stt":{"backend":"whisper","whisper_model":"unknown"}}),
        json!({"audio":{"sample_rate":48000}}),
        json!({"recording_retention":{"cutoff_date":"bad"}}),
        json!({"ai_models":{"openai":""}}),
        json!({"ai_models":{"unknown":"synthetic"}}),
    ] {
        assert_eq!(post_settings(&server, patch).await.0, 422);
    }
    assert!(!settings.data_dir.join("settings.toml").exists());
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn old_settings_are_ignored_without_changing_saved_meetings_or_recordings() {
    use meeting_storage::{models as db, Repository};
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "model");
    std::fs::create_dir_all(&settings.data_dir).unwrap();
    let old_settings = settings.data_dir.join("config.toml");
    let old_secrets = settings.data_dir.join("secrets.toml");
    let original =
        "[stt]\nbackend = 'deepgram'\n[ai.assignments]\nreply='codex'\nminutes='retired-route'\n";
    std::fs::write(&old_settings, original).unwrap();
    std::fs::write(&old_secrets, "OPENAI_API_KEY = 'synthetic-unused'\n").unwrap();
    seed_retention(
        &settings,
        "saved",
        db::Status::Completed,
        Some("2025-01-01T01:00:00Z"),
        128,
    )
    .await;
    let repo = Repository::open(&settings.data_dir.join("meeting_history.sqlite3"))
        .await
        .unwrap();
    repo.execute(db::Command::UpdateMeetingMinutes {
        meeting_id: "saved".into(),
        minutes: "synthetic saved minutes".into(),
    })
    .await
    .unwrap();
    repo.close().await;
    let recording = settings.data_dir.join("recordings/saved/other.wav");
    let recording_before = std::fs::read(&recording).unwrap();
    let server = Server::start(settings.clone()).await.unwrap();
    let (_, body) = http(&server, "GET", "/api/settings", "").await;
    let current: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(current["stt"]["backend"], "reazonspeech");
    assert_eq!(current["secrets"]["OPENAI_API_KEY"], false);
    let (_, body) = http(&server, "GET", "/api/ai/routes", "").await;
    let catalog: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(catalog["assignments"], json!({"reply":null}));
    let (status, body) = http(&server, "GET", "/meetings/saved", "").await;
    assert_eq!(status, 200);
    let detail: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(detail["minutes"], "synthetic saved minutes");
    assert_eq!(
        post_settings(&server, json!({"stt":{"silence_duration":0.8}}))
            .await
            .0,
        200
    );
    assert!(settings.data_dir.join("settings.toml").is_file());
    assert_eq!(std::fs::read_to_string(old_settings).unwrap(), original);
    assert_eq!(
        std::fs::read_to_string(old_secrets).unwrap(),
        "OPENAI_API_KEY = 'synthetic-unused'\n"
    );
    assert_eq!(std::fs::read(recording).unwrap(), recording_before);
    server.shutdown().await.unwrap();
    let server = Server::start(settings).await.unwrap();
    let (_, body) = http(&server, "GET", "/api/settings", "").await;
    let current: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(current["stt"]["silence_duration"], 0.8);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn saving_audio_settings_invalidates_prepared_inference_but_keeps_metering() {
    let temp = tempfile::tempdir().unwrap();
    let server = Server::start(config(&temp, "model")).await.unwrap();
    let mut ws = connect(&server).await;
    until(&mut ws, |v| v["type"] == "devices_list").await;
    send(&mut ws, json!({"type":"init_stt"})).await;
    until(&mut ws, |v| {
        v["type"] == "stt_state" && v["initialized"] == true
    })
    .await;
    assert_eq!(
        post_settings(&server, json!({"stt":{"silence_duration":0.8}}))
            .await
            .0,
        200
    );
    until(&mut ws, |v| {
        v["type"] == "stt_state" && v["initialized"] == false
    })
    .await;
    until(&mut ws, |v| {
        v["type"] == "audio_level" && v["level"].as_f64().is_some_and(|l| l > 0.0)
    })
    .await;
    send(&mut ws, json!({"type":"start_meeting"})).await;
    let error = until(&mut ws, |v| v["type"] == "error").await;
    assert!(error["text"].as_str().unwrap().contains("準備"));
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn settings_reads_remain_responsive_and_writes_conflict_during_preparation() {
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "hang");
    let server = Server::start(settings.clone()).await.unwrap();
    let mut ws = connect(&server).await;
    until(&mut ws, |v| v["type"] == "devices_list").await;
    send(&mut ws, json!({"type":"init_stt"})).await;
    tokio::time::timeout(std::time::Duration::from_secs(5), async {
        while !settings.model.as_ref().unwrap().join("preparing").exists() {
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    let response = tokio::time::timeout(
        std::time::Duration::from_secs(2),
        http(&server, "GET", "/api/settings", ""),
    )
    .await
    .unwrap();
    assert_eq!(response.0, 200);
    assert_eq!(
        post_settings(&server, json!({"stt":{"silence_duration":0.8}}))
            .await
            .0,
        409
    );
    assert!(!settings.data_dir.join("settings.toml").exists());
    drop(ws);
    server.shutdown().await.unwrap();
}

struct MockAi {
    url: String,
    requests: std::sync::Arc<std::sync::Mutex<Vec<Value>>>,
    peers: std::sync::Arc<std::sync::Mutex<Vec<std::net::SocketAddr>>>,
    task: tokio::task::JoinHandle<()>,
}
impl Drop for MockAi {
    fn drop(&mut self) {
        self.task.abort();
    }
}
async fn mock_ai(delay_ms: u64, finish: bool) -> MockAi {
    use axum::{
        extract::ConnectInfo,
        response::Sse,
        routing::{get, post},
        Json, Router,
    };
    let requests = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let captured = requests.clone();
    let peers = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let connections = peers.clone();
    let router=Router::new()
        .route("/v1/models",get(||async{Json(json!({"data":[{"id":"synthetic-model"}]}))}))
        .route("/v1/chat/completions",post(move |ConnectInfo(peer):ConnectInfo<std::net::SocketAddr>, Json(body):Json<Value>| {
            captured.lock().unwrap().push(body);
            connections.lock().unwrap().push(peer);
            async move {
                let frames=vec![
                    json!({"choices":[{"index":0,"delta":{"role":"assistant","content":"synthetic "},"finish_reason":null}]}),
                    json!({"choices":[{"index":0,"delta":{"content":"reply"},"finish_reason":null}]}),
                    json!({"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}),
                ];
                let count=if finish {3}else{1};
                Sse::new(futures_util::stream::unfold((frames,0),move |(frames,i)|async move {
                    if i>=count {return None;}
                    if i>0 {tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;}
                    let event=axum::response::sse::Event::default().data(frames[i].to_string());
                    Some((Ok::<_,std::convert::Infallible>(event),(frames,i+1)))
                }))
            }
        }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let task = tokio::spawn(async move {
        axum::serve(
            listener,
            router.into_make_service_with_connect_info::<std::net::SocketAddr>(),
        )
        .await
        .unwrap();
    });
    MockAi {
        url: format!("http://127.0.0.1:{port}/v1"),
        requests,
        peers,
        task,
    }
}
fn ai_config(temp: &tempfile::TempDir, ai: &MockAi, auto: bool) -> Config {
    let config = config(temp, "model");
    std::fs::create_dir_all(&config.data_dir).unwrap();
    std::fs::write(config.data_dir.join("settings.toml"),format!("schema_version = 1\n[ai.assignments]\nreply='ollama'\n[ai.routes.ollama]\nmodel='synthetic-model'\nbase_url='{}'\n[reply]\nauto_generate={auto}\n",ai.url)).unwrap();
    config
}
async fn manual_target(ws: &mut Socket) -> String {
    send(
        ws,
        json!({"type":"manual_speech","text":"synthetic target"}),
    )
    .await;
    until(ws, |v| {
        v["type"] == "stt_final" && v["text"] == "synthetic target"
    })
    .await["utterance_id"]
        .as_str()
        .unwrap()
        .into()
}
async fn saved_suggestions(server: &Server) -> Vec<Value> {
    let (_, body) = http(server, "GET", "/meetings", "").await;
    let list: Value = serde_json::from_slice(&body).unwrap();
    let id = list["items"][0]["id"].as_str().unwrap();
    let (_, body) = http(server, "GET", &format!("/meetings/{id}"), "").await;
    let detail: Value = serde_json::from_slice(&body).unwrap();
    detail["reply_suggestions"].as_array().unwrap().clone()
}
#[tokio::test]
async fn rig_reply_streams_saves_and_ignores_replayed_generation() {
    let ai = mock_ai(10, true).await;
    let temp = tempfile::tempdir().unwrap();
    let server = Server::start(ai_config(&temp, &ai, false)).await.unwrap();
    let (_, body) = http(&server, "GET", "/api/ai/routes", "").await;
    let catalog: Value = serde_json::from_slice(&body).unwrap();
    let ollama = catalog["routes"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["id"] == "ollama")
        .unwrap();
    assert_eq!(ollama["readiness"], "ready");
    assert_eq!(ollama["data_location"], "local");
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    let id = manual_target(&mut ws).await;
    let command = json!({"type":"generate_reply","generation_id":"synthetic-generation","target_utterance_id":id,"mode":"polite"});
    send(&mut ws, command.clone()).await;
    until(&mut ws, |v| v["type"] == "suggestions_start").await;
    let chunk = until(&mut ws, |v| {
        v["type"] == "reply_chunk" && v["final"] == false
    })
    .await;
    assert_eq!(chunk["text"], "synthetic ");
    until(&mut ws, |v| {
        v["type"] == "reply_chunk" && v["final"] == true
    })
    .await;
    send(&mut ws, command).await;
    send(&mut ws,json!({"type":"cancel_reply","generation_id":"synthetic-generation","target_utterance_id":id})).await;
    assert_eq!(
        until(&mut ws, |v| v["type"] == "reply_cancel_result").await["status"],
        "not_applied"
    );
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    let suggestions = saved_suggestions(&server).await;
    assert_eq!(suggestions.len(), 1);
    assert_eq!(suggestions[0]["text"], "synthetic reply");
    assert_eq!(ai.requests.lock().unwrap().len(), 1);
    let prompt = ai.requests.lock().unwrap()[0].to_string();
    assert!(prompt.contains("synthetic target"));
    assert!(prompt.contains("丁寧"));
    let (_, body) = http(&server, "GET", "/api/settings", "").await;
    let settings: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(settings["usage"]["current_meeting"]["request_count"], 1);
    assert_eq!(settings["usage"]["current_meeting"]["output_tokens"], 5);
    drop(ws);
    server.shutdown().await.unwrap();
}
#[tokio::test]
async fn reply_reuses_http_connection_and_reports_phase_timings() {
    let ai = mock_ai(50, true).await;
    let temp = tempfile::tempdir().unwrap();
    let server = Server::start(ai_config(&temp, &ai, false)).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    let target = manual_target(&mut ws).await;
    for generation in ["timing-one", "timing-two"] {
        send(&mut ws, json!({"type":"generate_reply","generation_id":generation,"target_utterance_id":target})).await;
        let timing = until(&mut ws, |v| {
            v["type"] == "reply_timing" && v["generation_id"] == generation
        })
        .await;
        assert_eq!(timing["outcome"], "completed");
        let preparation = timing["preparation_ms"].as_u64().unwrap();
        let first = timing["first_text_ms"].as_u64().unwrap();
        let sentence = timing["first_sentence_ms"].as_u64().unwrap();
        let total = timing["total_ms"].as_u64().unwrap();
        assert!(preparation <= first && first < sentence && sentence <= total);
        // The synthetic answer has no punctuation; its sentence completes at EOF.
        assert!(sentence - first >= 90);
        assert!(!timing.to_string().contains("synthetic"));
    }
    let peers = ai.peers.lock().unwrap().clone();
    assert_eq!(peers.len(), 2);
    assert_eq!(
        peers[0], peers[1],
        "successive requests must reuse a live TCP connection"
    );
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn rig_cancellation_reaps_stream_without_saving_partial_reply() {
    let ai = mock_ai(500, true).await;
    let temp = tempfile::tempdir().unwrap();
    let server = Server::start(ai_config(&temp, &ai, false)).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    let id = manual_target(&mut ws).await;
    send(
        &mut ws,
        json!({"type":"generate_reply","generation_id":"cancel-me","target_utterance_id":id}),
    )
    .await;
    until(&mut ws, |v| v["type"] == "reply_chunk").await;
    send(
        &mut ws,
        json!({"type":"cancel_reply","generation_id":"wrong","target_utterance_id":id}),
    )
    .await;
    assert_eq!(
        until(&mut ws, |v| v["type"] == "reply_cancel_result").await["status"],
        "not_applied"
    );
    send(
        &mut ws,
        json!({"type":"cancel_reply","generation_id":"cancel-me","target_utterance_id":id}),
    )
    .await;
    let timing = until(&mut ws, |v| v["type"] == "reply_timing").await;
    assert_eq!(timing["outcome"], "cancelled");
    assert!(timing["first_text_ms"].is_number());
    assert!(timing["first_sentence_ms"].is_null());
    assert_eq!(
        until(&mut ws, |v| v["type"] == "reply_cancel_result").await["status"],
        "applied"
    );
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    assert!(saved_suggestions(&server).await.is_empty());
    drop(ws);
    server.shutdown().await.unwrap();
}
#[tokio::test]
async fn rig_truncated_stream_is_an_error_and_recording_still_completes() {
    let ai = mock_ai(0, false).await;
    let temp = tempfile::tempdir().unwrap();
    let server = Server::start(ai_config(&temp, &ai, false)).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    let id = manual_target(&mut ws).await;
    send(
        &mut ws,
        json!({"type":"generate_reply","generation_id":"truncated","target_utterance_id":id}),
    )
    .await;
    until(&mut ws, |v| v["type"] == "suggestion_error").await;
    let timing = until(&mut ws, |v| v["type"] == "reply_timing").await;
    assert_eq!(timing["outcome"], "failed");
    assert!(timing["first_sentence_ms"].is_null());
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    let state = until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    assert_eq!(state["saved"], true);
    assert!(saved_suggestions(&server).await.is_empty());
    drop(ws);
    server.shutdown().await.unwrap();
}
#[tokio::test]
async fn rig_automatic_reply_is_opt_in() {
    let ai = mock_ai(0, true).await;
    let temp = tempfile::tempdir().unwrap();
    let server = Server::start(ai_config(&temp, &ai, true)).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    manual_target(&mut ws).await;
    until(&mut ws, |v| {
        v["type"] == "reply_chunk" && v["final"] == true
    })
    .await;
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    assert_eq!(
        ai.requests.lock().unwrap().len(),
        1,
        "final drained transcripts must not start another generation"
    );
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn retired_info_and_minutes_cannot_be_reenabled() {
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "model");
    std::fs::create_dir_all(&settings.data_dir).unwrap();
    let path = settings.data_dir.join("settings.toml");
    let original = "schema_version = 1\n";
    std::fs::write(&path, original).unwrap();
    let server = Server::start(settings).await.unwrap();

    let (status, body) = http(&server, "GET", "/api/settings", "").await;
    assert_eq!(status, 200);
    let settings: Value = serde_json::from_slice(&body).unwrap();
    assert!(settings.get("agents").is_none());
    let (status, body) = http(&server, "GET", "/api/ai/routes", "").await;
    assert_eq!(status, 200);
    let catalog: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(catalog["assignments"], json!({"reply":null}));
    assert!(catalog["routes"].as_array().unwrap().iter().all(|route| {
        !route["capabilities"]
            .as_array()
            .unwrap()
            .iter()
            .any(|capability| capability == "info" || capability == "minutes")
    }));
    for (method, path, value) in [
        (
            "POST",
            "/api/settings",
            json!({"agents":{"info_enabled":true}}),
        ),
        (
            "PUT",
            "/api/ai/routes/assignments",
            json!({"reply":null,"info":"ollama"}),
        ),
    ] {
        let body = value.to_string();
        let extra = format!(
            "Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            body.len(),
            body
        );
        assert_eq!(http(&server, method, path, &extra).await.0, 422);
    }
    let mut ws = connect(&server).await;
    let agents = until(&mut ws, |v| v["type"] == "agent_settings").await;
    assert!(agents.get("info_enabled").is_none());
    send(&mut ws, json!({"type":"run_info"})).await;
    let error = until(&mut ws, |v| v["type"] == "error").await;
    assert_eq!(error["text"], "未対応の操作、または不正なメッセージです。");
    assert_eq!(std::fs::read_to_string(path).unwrap(), original);
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn ai_assignments_persist_without_enabling_unported_routes() {
    let ai = mock_ai(0, true).await;
    let temp = tempfile::tempdir().unwrap();
    let config = ai_config(&temp, &ai, false);
    let server = Server::start(config.clone()).await.unwrap();
    for (value, expected) in [
        (json!({"reply":"codex"}), 422),
        (json!({"reply":"acp"}), 422),
        (json!({"reply":null,"minutes":"gemini"}), 422),
        (json!({"reply":"ollama","info":"ollama"}), 422),
        (json!({"reply":"gemini"}), 200),
    ] {
        let body = value.to_string();
        let extra = format!(
            "Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            body.len(),
            body
        );
        assert_eq!(
            http(&server, "PUT", "/api/ai/routes/assignments", &extra)
                .await
                .0,
            expected
        );
    }
    server.shutdown().await.unwrap();
    let server = Server::start(config).await.unwrap();
    let (_, body) = http(&server, "GET", "/api/ai/routes", "").await;
    let catalog: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(catalog["assignments"]["reply"], "gemini");
    let rows = catalog["routes"].as_array().unwrap();
    assert!(rows.iter().all(|r| r["id"] != "codex" && r["id"] != "acp"));
    assert_eq!(
        rows.iter().find(|r| r["id"] == "gemini").unwrap()["readiness"],
        "setup_required"
    );
    assert_eq!(
        rows.iter().find(|r| r["id"] == "managed").unwrap()["readiness"],
        "not_offered"
    );
    server.shutdown().await.unwrap();
}
#[tokio::test]
async fn multiple_reply_styles_finish_and_stop_meeting_cancels_pending_generation() {
    let ai = mock_ai(0, true).await;
    let temp = tempfile::tempdir().unwrap();
    let config = ai_config(&temp, &ai, false);
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .append(true)
        .open(config.data_dir.join("settings.toml"))
        .unwrap();
    file.write_all(b"[[reply.styles]]\nid='standard'\nlabel='Standard'\nenabled=true\npriority=10\n[[reply.styles]]\nid='second'\nlabel='Second'\nenabled=true\npriority=20\ninstruction='synthetic second style'\n").unwrap();
    let server = Server::start(config).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    let id = manual_target(&mut ws).await;
    send(
        &mut ws,
        json!({"type":"generate_reply","generation_id":"multi","target_utterance_id":id}),
    )
    .await;
    until(&mut ws, |v| {
        v["type"] == "reply_chunk" && v["final"] == true && v["agent_id"] == "second"
    })
    .await;
    assert_eq!(ai.requests.lock().unwrap().len(), 2);
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    assert_eq!(saved_suggestions(&server).await.len(), 2);
    drop(ws);
    server.shutdown().await.unwrap();

    let ai = mock_ai(5000, true).await;
    let temp = tempfile::tempdir().unwrap();
    let server = Server::start(ai_config(&temp, &ai, false)).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    let id = manual_target(&mut ws).await;
    send(
        &mut ws,
        json!({"type":"generate_reply","generation_id":"pending","target_utterance_id":id}),
    )
    .await;
    until(&mut ws, |v| v["type"] == "reply_chunk").await;
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    assert!(saved_suggestions(&server).await.is_empty());
    // A new meeting has fresh generation state and cannot accept an old target.
    send(&mut ws, json!({"type":"init_stt"})).await;
    until(&mut ws, |v| {
        v["type"] == "stt_state" && v["initialized"] == true
    })
    .await;
    send(&mut ws, json!({"type":"start_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == true
    })
    .await;
    send(
        &mut ws,
        json!({"type":"generate_reply","generation_id":"old-target","target_utterance_id":id}),
    )
    .await;
    until(&mut ws, |v| {
        v["type"] == "error"
            && v["text"]
                .as_str()
                .unwrap_or("")
                .contains("返答案を作れる発言")
    })
    .await;
    assert_eq!(ai.requests.lock().unwrap().len(), 1);
    drop(ws);
    server.shutdown().await.unwrap();
}

async fn json_request(server: &Server, path: &str, value: Value) -> (u16, Value) {
    let body = value.to_string();
    let extra = format!(
        "Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
        body.len(),
        body
    );
    let (status, body) = http(server, "POST", path, &extra).await;
    (status, serde_json::from_slice(&body).unwrap())
}
async fn seed_retention(
    config: &Config,
    id: &str,
    status: meeting_storage::models::Status,
    ended: Option<&str>,
    bytes: i64,
) {
    use meeting_storage::{models as db, Repository};
    let repo = Repository::open(&config.data_dir.join("meeting_history.sqlite3"))
        .await
        .unwrap();
    let timestamp = |s: &str| serde_json::from_value::<db::Timestamp>(json!(s)).unwrap();
    repo.execute(db::Command::CreateMeeting {
        record: db::Meeting {
            id: id.into(),
            started_at: timestamp("2025-01-01T00:00:00Z"),
            status,
            ended_at: ended.map(timestamp),
            duration_seconds: None,
            title: None,
            ai_note: String::new(),
            minutes: String::new(),
            created_at: None,
            updated_at: None,
        },
    })
    .await
    .unwrap();
    if bytes > 0 {
        repo.execute(db::Command::InsertRecordingAssets {
            records: vec![db::Asset {
                id: format!("asset-{id}"),
                meeting_id: id.into(),
                role: db::Role::Other,
                relative_path: format!("recordings/{id}/other.wav"),
                format: db::Format::Wav,
                sample_rate: 16000,
                channels: 1,
                started_at: timestamp("2025-01-01T00:00:00Z"),
                ended_at: ended.map(timestamp),
                size_bytes: Some(bytes),
            }],
        })
        .await
        .unwrap();
        let dir = config.data_dir.join("recordings").join(id);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("other.wav"), vec![0; bytes as usize]).unwrap();
    }
    let materials = config.data_dir.join("meetings").join(id).join("references");
    std::fs::create_dir_all(&materials).unwrap();
    std::fs::write(materials.join("synthetic.txt"), "synthetic reference").unwrap();
    repo.close().await;
}
#[tokio::test]
async fn cleanup_preview_is_inert_and_execute_removes_completed_meetings_and_references_only() {
    use meeting_storage::models::Status;
    let temp = tempfile::tempdir().unwrap();
    let config = config(&temp, "model");
    let server = Server::start(config.clone()).await.unwrap();
    seed_retention(
        &config,
        "old",
        Status::Completed,
        Some("2025-01-01T00:00:00Z"),
        120,
    )
    .await;
    seed_retention(
        &config,
        "new",
        Status::Completed,
        Some("2025-01-03T00:00:00Z"),
        80,
    )
    .await;
    seed_retention(
        &config,
        "empty",
        Status::Completed,
        Some("2025-01-01T01:00:00Z"),
        0,
    )
    .await;
    seed_retention(&config, "active", Status::Active, None, 300).await;
    seed_retention(
        &config,
        "aborted",
        Status::Aborted,
        Some("2025-01-01T00:00:00Z"),
        400,
    )
    .await;
    assert_eq!(
        post_settings(
            &server,
            json!({"recording_retention":{"cutoff_date":"2026-01-01","max_total_bytes":1}})
        )
        .await
        .0,
        200
    );
    assert!(
        config.data_dir.join("recordings/old/other.wav").exists(),
        "saving settings must not delete recordings"
    );
    let policy = json!({"cutoff_date":"2025-01-02","max_total_bytes":50});
    let (status, preview) = json_request(
        &server,
        "/meetings/recordings/cleanup/preview",
        policy.clone(),
    )
    .await;
    assert_eq!(status, 200);
    assert_eq!(
        preview["candidate_meeting_ids"],
        json!(["old", "empty", "new"])
    );
    assert_eq!(preview["total_recording_bytes_before"], 200);
    assert_eq!(preview["total_recording_bytes_after"], 0);
    assert!(config.data_dir.join("recordings/old/other.wav").exists());
    let (status, result) =
        json_request(&server, "/meetings/recordings/cleanup", policy.clone()).await;
    assert_eq!(status, 200);
    assert_eq!(
        result["deleted_meeting_ids"],
        preview["candidate_meeting_ids"]
    );
    assert_eq!(result["failed_meeting_ids"], json!([]));
    for id in ["old", "new", "empty"] {
        assert!(!config.data_dir.join("meetings").join(id).exists());
        assert!(!config.data_dir.join("recordings").join(id).exists());
        assert_eq!(
            http(&server, "GET", &format!("/meetings/{id}"), "").await.0,
            404
        );
    }
    for id in ["active", "aborted"] {
        assert!(config
            .data_dir
            .join("recordings")
            .join(id)
            .join("other.wav")
            .exists());
    }
    assert_eq!(
        json_request(&server, "/meetings/recordings/cleanup", policy)
            .await
            .0,
        409,
        "execution consumes its preview"
    );
    server.shutdown().await.unwrap();
}
#[tokio::test]
async fn cleanup_rejects_stale_preview_and_handles_utc_cutoff_boundaries() {
    use meeting_storage::models::Status;
    let temp = tempfile::tempdir().unwrap();
    let config = config(&temp, "model");
    let server = Server::start(config.clone()).await.unwrap();
    seed_retention(
        &config,
        "before",
        Status::Completed,
        Some("2025-01-02T08:59:59+09:00"),
        50,
    )
    .await;
    seed_retention(
        &config,
        "at",
        Status::Completed,
        Some("2025-01-02T00:00:00"),
        50,
    )
    .await;
    let policy = json!({"cutoff_date":"2025-01-02","max_total_bytes":0});
    let (_, preview) = json_request(
        &server,
        "/meetings/recordings/cleanup/preview",
        policy.clone(),
    )
    .await;
    assert_eq!(preview["candidate_meeting_ids"], json!(["before"]));
    seed_retention(
        &config,
        "newly-completed",
        Status::Completed,
        Some("2025-01-01T00:00:00Z"),
        30,
    )
    .await;
    assert_eq!(
        json_request(&server, "/meetings/recordings/cleanup", policy.clone())
            .await
            .0,
        409
    );
    for id in ["before", "at", "newly-completed"] {
        assert!(config.data_dir.join("recordings").join(id).exists());
    }
    let (_, preview) = json_request(
        &server,
        "/meetings/recordings/cleanup/preview",
        policy.clone(),
    )
    .await;
    assert_eq!(
        preview["candidate_meeting_ids"],
        json!(["newly-completed", "before"])
    );
    assert_eq!(
        json_request(&server, "/meetings/recordings/cleanup", policy)
            .await
            .0,
        200
    );
    assert!(config.data_dir.join("recordings/at").exists());
    for policy in [
        json!({}),
        json!({"max_total_bytes":0}),
        json!({"cutoff_date":"2025-02-30"}),
    ] {
        assert_eq!(
            json_request(&server, "/meetings/recordings/cleanup/preview", policy)
                .await
                .0,
            400
        );
    }
    server.shutdown().await.unwrap();
}
#[cfg(unix)]
#[tokio::test]
async fn cleanup_reports_per_meeting_failure_and_never_follows_linked_directories() {
    use meeting_storage::models::Status;
    let temp = tempfile::tempdir().unwrap();
    let config = config(&temp, "model");
    let server = Server::start(config.clone()).await.unwrap();
    for id in ["blocked", "safe"] {
        seed_retention(
            &config,
            id,
            Status::Completed,
            Some("2025-01-01T00:00:00Z"),
            20,
        )
        .await;
    }
    let external = temp.path().join("outside");
    std::fs::create_dir(&external).unwrap();
    std::fs::write(external.join("sentinel"), b"synthetic protected").unwrap();
    // A linked materials directory is detected before removing this meeting's WAVs.
    let linked = config.data_dir.join("meetings/blocked");
    std::fs::remove_dir_all(&linked).unwrap();
    std::os::unix::fs::symlink(&external, &linked).unwrap();
    // Child links are unlinked as entries, never traversed.
    std::os::unix::fs::symlink(&external, config.data_dir.join("recordings/safe/external"))
        .unwrap();
    let policy = json!({"cutoff_date":"2025-01-02"});
    assert_eq!(
        json_request(
            &server,
            "/meetings/recordings/cleanup/preview",
            policy.clone()
        )
        .await
        .0,
        200
    );
    let (_, result) = json_request(&server, "/meetings/recordings/cleanup", policy).await;
    assert_eq!(result["failed_meeting_ids"], json!(["blocked"]));
    assert_eq!(result["deleted_meeting_ids"], json!(["safe"]));
    assert!(external.join("sentinel").exists());
    assert!(config
        .data_dir
        .join("recordings/blocked/other.wav")
        .exists());
    assert_eq!(http(&server, "GET", "/meetings/blocked", "").await.0, 200);
    assert_eq!(
        http(&server, "DELETE", "/meetings/blocked", "").await.0,
        409
    );
    server.shutdown().await.unwrap();
}
fn synthetic_docx(text: &str) -> String {
    use base64::Engine;
    use std::io::{Cursor, Write};
    let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
    zip.start_file(
        "word/document.xml",
        zip::write::SimpleFileOptions::default(),
    )
    .unwrap();
    write!(zip,"<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>").unwrap();
    zip.start_file(
        "word/media/synthetic.bin",
        zip::write::SimpleFileOptions::default(),
    )
    .unwrap();
    zip.write_all(&vec![0; 900_000]).unwrap();
    base64::engine::general_purpose::STANDARD.encode(zip.finish().unwrap().into_inner())
}
#[tokio::test]
async fn existing_ui_reference_payloads_persist_and_reach_rig_with_synthetic_converter() {
    reference_flow(env!("CARGO_BIN_EXE_synthetic-worker").into()).await;
}
#[tokio::test]
#[ignore = "requires a built onedir worker; set MEETING_TEST_PYTHON_WORKER"]
async fn frozen_markitdown_worker_converts_persists_and_reaches_rig() {
    reference_flow(
        std::env::var_os("MEETING_TEST_PYTHON_WORKER")
            .expect("set the frozen worker path")
            .into(),
    )
    .await;
}
async fn reference_flow(worker: std::path::PathBuf) {
    let ai = mock_ai(0, true).await;
    let temp = tempfile::tempdir().unwrap();
    let mut config = ai_config(&temp, &ai, false);
    config.python_worker = worker;
    std::fs::create_dir_all(config.data_dir.join("context")).unwrap();
    std::fs::write(
        config.data_dir.join("context/profile.md"),
        "synthetic global context",
    )
    .unwrap();
    let server = Server::start(config.clone()).await.unwrap();
    let mut ws = connect(&server).await;
    until(&mut ws, |v| v["type"] == "devices_list").await;
    send(&mut ws, json!({"type":"init_stt"})).await;
    until(&mut ws, |v| {
        v["type"] == "stt_state" && v["initialized"] == true
    })
    .await;
    send(&mut ws,json!({"type":"start_meeting","meeting_context":{"objective":"synthetic objective"},"references":[
        {"id":"md","name":"notes.md","mimeType":"text/markdown","sizeBytes":30,"text":"synthetic markdown material","status":"parsed","error":null},
        {"id":"docx","name":"outline.docx","mimeType":"application/vnd.openxmlformats-officedocument.wordprocessingml.document","sizeBytes":900300,"contentBase64":synthetic_docx("synthetic docx material"),"status":"queued","error":null},
        {"id":"bad","name":"broken.docx","mimeType":"application/octet-stream","sizeBytes":3,"contentBase64":"bad","status":"queued","error":null}
    ]})).await;
    let id = until(&mut ws, |v| {
        v["type"] == "session_info" && v["is_active"] == true
    })
    .await["id"]
        .as_str()
        .unwrap()
        .to_owned();
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == true
    })
    .await;
    let target = manual_target(&mut ws).await;
    send(
        &mut ws,
        json!({"type":"generate_reply","generation_id":"references","target_utterance_id":target}),
    )
    .await;
    until(&mut ws, |v| {
        v["type"] == "reply_chunk" && v["final"] == true
    })
    .await;
    let prompt = ai.requests.lock().unwrap()[0].to_string();
    for text in [
        "synthetic markdown material",
        "synthetic docx material",
        "synthetic global context",
        "synthetic objective",
    ] {
        assert!(prompt.contains(text), "reference must reach the request");
    }
    assert!(!prompt.contains("broken.docx"));
    let directory = config
        .data_dir
        .join("meetings")
        .join(&id)
        .join("references");
    assert!(directory.join("md/parsed.md").exists());
    assert_eq!(
        std::fs::read_to_string(directory.join("docx/parsed.md")).unwrap(),
        "synthetic docx material"
    );
    let failed: Value =
        serde_json::from_slice(&std::fs::read(directory.join("bad/metadata.json")).unwrap())
            .unwrap();
    assert_eq!(failed["status"], "failed");
    std::fs::write(
        config.data_dir.join("context/profile.md"),
        "synthetic refreshed context",
    )
    .unwrap();
    send(&mut ws, json!({"type":"reload_context"})).await;
    until(&mut ws, |v| {
        v["type"] == "status" && v["text"] == "参考情報を再読み込みしました。"
    })
    .await;
    send(
        &mut ws,
        json!({"type":"generate_reply","generation_id":"refreshed","target_utterance_id":target}),
    )
    .await;
    until(&mut ws, |v| {
        v["type"] == "reply_chunk" && v["final"] == true
    })
    .await;
    assert!(ai.requests.lock().unwrap()[1]
        .to_string()
        .contains("synthetic refreshed context"));
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    assert!(directory.join("md/parsed.md").exists());
    delete_when_idle(&server, &id).await;
    assert!(!directory.exists());
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn model_api_reuses_huggingface_snapshot_and_rejects_removed_backend() {
    let temp = tempfile::tempdir().unwrap();
    let mut settings = config(&temp, "local-unused");
    settings.model = None;
    let snapshot = settings.hub_cache.join("models--reazon-research--reazonspeech-k2-v2/snapshots/291488c8151be24d7da4bf7af26e533fad96e407");
    std::fs::create_dir_all(&snapshot).unwrap();
    for file in [
        "tokens.txt",
        "encoder-epoch-99-avg-1.int8.onnx",
        "decoder-epoch-99-avg-1.int8.onnx",
        "joiner-epoch-99-avg-1.int8.onnx",
    ] {
        std::fs::write(snapshot.join(file), "synthetic").unwrap();
    }
    let server = Server::start(settings).await.unwrap();
    let (code, body) = http(
        &server,
        "GET",
        "/api/stt/model?backend=reazonspeech&language=ja",
        "",
    )
    .await;
    assert_eq!(code, 200);
    let status: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(status["state"], "ready");
    assert_eq!(status["model_path"], snapshot.to_str().unwrap());
    assert_eq!(
        http(
            &server,
            "GET",
            "/api/stt/model?backend=vosk&language=ja",
            ""
        )
        .await
        .0,
        400
    );
    let (code, body) = http(
        &server,
        "POST",
        "/api/stt/model/cancel?backend=reazonspeech&language=ja",
        "Content-Length: 0\r\n",
    )
    .await;
    assert_eq!(code, 200);
    assert_eq!(
        serde_json::from_slice::<Value>(&body).unwrap()["state"],
        "ready"
    );
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn whisper_shares_one_worker_and_reuses_it_across_meetings() {
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "model");
    let root = settings.hub_cache.join("models--ggerganov--whisper.cpp");
    let revision = "5359861c739e955e79d9a303bcbc70fb988958b1";
    let snapshot = root.join("snapshots").join(revision);
    std::fs::create_dir_all(&snapshot).unwrap();
    std::fs::write(
        snapshot.join("ggml-tiny-q8_0.bin"),
        b"synthetic ggml fixture",
    )
    .unwrap();
    let server = Server::start(settings).await.unwrap();
    assert_eq!(post_settings(&server, json!({"stt":{"backend":"whisper","whisper_model":"tiny","device":"cpu","language":"auto"}})).await.0, 200);
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    for meeting in 0..2 {
        if meeting > 0 {
            send(&mut ws, json!({"type":"start_meeting","meeting_context":{"scenario":"synthetic","objective":"reuse"},"references":[]})).await;
            until(&mut ws, |v| {
                v["type"] == "meeting_state" && v["running"] == true
            })
            .await;
            wait_for_both_audio_inputs(&mut ws).await;
        }
        send(&mut ws, json!({"type":"stop_meeting"})).await;
        assert_eq!(
            until(&mut ws, |v| v["type"] == "meeting_state"
                && v["running"] == false)
            .await["saved"],
            true
        );
    }
    let pids: Vec<_> = std::fs::read_dir(&snapshot)
        .unwrap()
        .flatten()
        .filter(|f| f.file_name().to_string_lossy().starts_with("pid-"))
        .collect();
    assert_eq!(
        pids.len(),
        1,
        "both inputs and meetings reuse one prepared model"
    );
    for pid in &pids {
        let args = std::fs::read_to_string(snapshot.join(format!(
            "args-{}.json",
            pid.file_name().to_string_lossy().trim_start_matches("pid-")
        )))
        .unwrap();
        assert!(
            args.contains("--shared")
                && args.contains("--whisper-model")
                && args.contains("--inference-device")
                && args.contains("cpu")
                && args.contains("auto")
        );
        assert!(!args.contains("--punctuation-model"));
    }
    let (_, body) = http(&server, "GET", "/meetings", "").await;
    let page: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(page["total"], 2);
    for item in page["items"].as_array().unwrap() {
        let (_, body) = http(
            &server,
            "GET",
            &format!("/meetings/{}", item["id"].as_str().unwrap()),
            "",
        )
        .await;
        let detail: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(detail["turns"].as_array().unwrap().len(), 2);
    }
    drop(ws);
    server.shutdown().await.unwrap();
    for pid in pids {
        assert!(!std::path::Path::new("/proc")
            .join(pid.file_name().to_string_lossy().trim_start_matches("pid-"))
            .exists());
    }
}

#[tokio::test]
async fn stopping_detaches_both_inputs_before_shared_inference_drain() {
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "slow-finish");
    let model = settings.model.clone().unwrap();
    let server = Server::start(settings).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    let started = std::time::Instant::now();
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    let status = until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    assert_eq!(status["saved"], true);
    assert!(
        started.elapsed() < std::time::Duration::from_millis(5800),
        "both inputs must drain through the shared worker"
    );
    let counts: Vec<usize> = std::fs::read_dir(model)
        .unwrap()
        .flatten()
        .filter(|entry| {
            entry
                .file_name()
                .to_string_lossy()
                .starts_with("finished-samples-")
        })
        .map(|entry| {
            std::fs::read_to_string(entry.path())
                .unwrap()
                .parse()
                .unwrap()
        })
        .collect();
    assert_eq!(counts.len(), 2);
    assert!(
        counts.iter().all(|count| *count > 0 && *count < 24000),
        "neither input may capture during the two-second drain: {counts:?}"
    );
    drop(ws);
    server.shutdown().await.unwrap();
}

async fn agent_request(server: &Server, method: &str, path: &str, value: Value) -> (u16, Value) {
    let body = value.to_string();
    let extra = format!(
        "Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
        body.len(),
        body
    );
    let (status, body) = http(server, method, path, &extra).await;
    (status, serde_json::from_slice(&body).unwrap())
}
fn acp_config(temp: &tempfile::TempDir) -> (Config, std::path::PathBuf) {
    let config = config(temp, "model");
    let root = config.data_dir.join("agents");
    let installed = root.join("synthetic-1");
    std::fs::create_dir_all(&installed).unwrap();
    std::fs::copy(env!("CARGO_BIN_EXE_synthetic-acp"), installed.join("agent")).unwrap();
    let marker = temp.path().join("process-events");
    let entry = json!({"id":"synthetic","name":"Synthetic Agent","version":"1.0.0","description":"Synthetic fixture","authors":[],"license":"MIT","distribution":{"binary":{},"npx":null,"uvx":null}});
    std::fs::write(root.join("installed.json"), json!({"synthetic":{"entry":entry,"directory":"synthetic-1","executable":"agent","args":[marker],"env":{},"node":false}}).to_string()).unwrap();
    std::fs::write(config.data_dir.join("settings.toml"), "schema_version = 1\n[reply]\nenabled = true\nauto_generate = false\n[[reply.styles]]\nid = 'standard'\nlabel = '標準'\nenabled = true\npriority = 1\ninstruction = '合成テスト'\n").unwrap();
    (config, marker)
}

#[tokio::test]
async fn old_agent_settings_do_not_change_installed_registry_agents() {
    let temp = tempfile::tempdir().unwrap();
    let (config, _) = acp_config(&temp);
    let manifest = config.data_dir.join("agents/installed.json");
    let installed = std::fs::read(&manifest).unwrap();
    let old = config.data_dir.join("config.toml");
    let original = "[ai.assignments]\nreply='codex'\n[ai.routes.acp]\ncommand=123\n";
    std::fs::write(&old, original).unwrap();
    let server = Server::start(config).await.unwrap();
    let (_, body) = http(&server, "GET", "/api/ai/routes", "").await;
    let catalog: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(catalog["assignments"], json!({"reply":null}));
    let rows = catalog["routes"].as_array().unwrap();
    assert!(rows
        .iter()
        .all(|route| route["id"] != "codex" && route["id"] != "acp"));
    assert!(rows.iter().any(|route| route["id"] == "acp:synthetic"));
    assert_eq!(
        post_settings(&server, json!({"reply":{"auto_generate":false}}))
            .await
            .0,
        200
    );
    assert_eq!(std::fs::read_to_string(old).unwrap(), original);
    assert_eq!(std::fs::read(manifest).unwrap(), installed);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn acp_authentication_streaming_process_reuse_and_meeting_lock() {
    let temp = tempfile::tempdir().unwrap();
    let (config, marker) = acp_config(&temp);
    let server = Server::start(config).await.unwrap();
    let (status, updates) =
        agent_request(&server, "POST", "/api/ai/agents/update-all", json!({})).await;
    assert_eq!(status, 200);
    assert_eq!(updates["results"], json!([]));
    let (status, body) = agent_request(
        &server,
        "POST",
        "/api/ai/agents/synthetic/connect",
        json!({}),
    )
    .await;
    assert_eq!(status, 200);
    assert_eq!(body["ready"], false);
    assert_eq!(body["auth_methods"][0]["id"], "synthetic-login");
    assert!(!body.to_string().contains("synthetic secret"));
    assert_eq!(
        agent_request(
            &server,
            "PUT",
            "/api/ai/routes/assignments",
            json!({"reply":"acp:synthetic"})
        )
        .await
        .0,
        422
    );
    let (status, body) = agent_request(
        &server,
        "POST",
        "/api/ai/agents/synthetic/connect",
        json!({"method":"synthetic-login"}),
    )
    .await;
    assert_eq!(status, 200);
    assert_eq!(body["ready"], true);
    assert_eq!(body["model"]["current"], "synthetic-fast");
    assert_eq!(body["model"]["options"].as_array().unwrap().len(), 3);
    assert_eq!(body["thought_level"]["current"], "deliberate");
    let (status, body) = agent_request(
        &server,
        "PUT",
        "/api/ai/agents/synthetic/model",
        json!({"model":"synthetic-accurate"}),
    )
    .await;
    assert_eq!(status, 200);
    assert_eq!(body["model"]["current"], "synthetic-accurate");
    let manifest: Value = serde_json::from_slice(
        &std::fs::read(temp.path().join("data/agents/installed.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(manifest["synthetic"]["model"], "synthetic-accurate");
    let (status, body) = agent_request(
        &server,
        "PUT",
        "/api/ai/agents/synthetic/thought-level",
        json!({"thought_level":"brief"}),
    )
    .await;
    assert_eq!(status, 200);
    assert_eq!(body["thought_level"]["current"], "brief");
    let saved: Value = serde_json::from_slice(
        &std::fs::read(temp.path().join("data/agents/installed.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(saved["synthetic"]["thought_level"], "brief");

    assert_eq!(
        agent_request(
            &server,
            "PUT",
            "/api/ai/routes/assignments",
            json!({"reply":"acp:synthetic"})
        )
        .await
        .0,
        200
    );
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    assert_eq!(
        agent_request(
            &server,
            "PUT",
            "/api/ai/agents/synthetic/thought-level",
            json!({"thought_level":"deliberate"})
        )
        .await
        .0,
        409
    );
    assert_eq!(
        agent_request(&server, "POST", "/api/ai/agents/update-all", json!({}))
            .await
            .0,
        409
    );
    assert_eq!(
        http(&server, "GET", "/api/ai/agents?refresh=true", "")
            .await
            .0,
        409
    );
    assert_eq!(
        agent_request(
            &server,
            "POST",
            "/api/ai/agents/synthetic/install",
            json!({})
        )
        .await
        .0,
        409
    );
    assert_eq!(
        http(&server, "DELETE", "/api/ai/agents/synthetic", "")
            .await
            .0,
        409
    );
    let target = manual_target(&mut ws).await;
    for (index, generation) in ["first", "second"].into_iter().enumerate() {
        send(&mut ws, json!({"type":"generate_reply","generation_id":generation,"target_utterance_id":target})).await;
        let chunk = until(&mut ws, |v| {
            v["type"] == "reply_chunk" && v["final"] == false
        })
        .await;
        // Preparation includes both selections and happens before another request.
        tokio::time::timeout(std::time::Duration::from_secs(5), async {
            loop {
                let events = std::fs::read_to_string(&marker).unwrap();
                if events
                    .lines()
                    .filter(|line| *line == "thought:brief")
                    .count()
                    == index + 2
                {
                    break;
                }
                tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("the next ACP session must be prepared without another generation");
        assert_eq!(chunk["text"], "合成の返答です。");
        until(&mut ws, |v| {
            v["type"] == "reply_chunk" && v["final"] == true
        })
        .await;
    }
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    let suggestions = saved_suggestions(&server).await;
    assert_eq!(suggestions.len(), 2);
    assert!(suggestions.iter().all(|s| s["text"] == "合成の返答です。"));
    let (_, usage) = http(&server, "GET", "/api/settings", "").await;
    let usage: Value = serde_json::from_slice(&usage).unwrap();
    assert_eq!(usage["usage"]["current_month"]["incomplete_requests"], 2);
    let events = std::fs::read_to_string(marker).unwrap();
    assert_eq!(events.lines().filter(|l| *l == "start").count(), 1);
    assert_eq!(events.lines().filter(|l| *l == "session").count(), 3);
    assert_eq!(
        events
            .lines()
            .filter(|l| l.starts_with("prompt:"))
            .collect::<Vec<_>>(),
        vec!["prompt:session-1", "prompt:session-2"]
    );
    assert_eq!(
        events
            .lines()
            .filter(|line| *line == "model:synthetic-accurate")
            .count(),
        3,
        "the persisted selection is applied to each new ACP session"
    );
    assert_eq!(
        events
            .lines()
            .filter(|line| *line == "thought:brief")
            .count(),
        3,
        "the reasoning preference is applied through the advertised config ID on each session"
    );

    assert_eq!(
        http(&server, "DELETE", "/api/ai/agents/synthetic", "")
            .await
            .0,
        409
    );
    assert_eq!(
        agent_request(
            &server,
            "PUT",
            "/api/ai/routes/assignments",
            json!({"reply":null})
        )
        .await
        .0,
        200
    );
    assert_eq!(
        http(&server, "DELETE", "/api/ai/agents/synthetic", "")
            .await
            .0,
        200
    );
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn acp_prepares_next_session_without_waiting_for_close_and_reauthenticates_it() {
    let temp = tempfile::tempdir().unwrap();
    let (config, marker) = acp_config(&temp);
    let manifest = config.data_dir.join("agents/installed.json");
    let gate = temp.path().join("close-gate");
    let mut installed: Value = serde_json::from_slice(&std::fs::read(&manifest).unwrap()).unwrap();
    installed["synthetic"]["env"] = json!({"SYNTHETIC_CLOSE_GATE":gate});
    std::fs::write(&manifest, installed.to_string()).unwrap();
    let server = Server::start(config).await.unwrap();
    assert_eq!(
        agent_request(
            &server,
            "POST",
            "/api/ai/agents/synthetic/connect",
            json!({"method":"synthetic-login"})
        )
        .await
        .0,
        200
    );
    assert_eq!(
        agent_request(
            &server,
            "PUT",
            "/api/ai/routes/assignments",
            json!({"reply":"acp:synthetic"})
        )
        .await
        .0,
        200
    );
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    let target = manual_target(&mut ws).await;
    send(
        &mut ws,
        json!({"type":"generate_reply","generation_id":"prepared","target_utterance_id":target}),
    )
    .await;
    let timing = until(&mut ws, |v| v["type"] == "reply_timing").await;
    assert_eq!(timing["outcome"], "completed");
    assert_eq!(timing["first_text_ms"], timing["first_sentence_ms"]);
    assert!(
        timing["total_ms"].as_u64().unwrap() < 1500,
        "the two-second close timeout must not delay completion"
    );
    tokio::time::timeout(std::time::Duration::from_secs(5), async {
        loop {
            if std::fs::read_to_string(&marker)
                .unwrap()
                .lines()
                .filter(|line| *line == "session")
                .count()
                == 2
            {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    assert!(!gate.exists());
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    std::fs::write(&gate, b"release").unwrap();
    assert_eq!(
        agent_request(
            &server,
            "POST",
            "/api/ai/agents/synthetic/connect",
            json!({"method":"synthetic-alternate"})
        )
        .await
        .0,
        200
    );
    let events = std::fs::read_to_string(&marker).unwrap();
    assert_eq!(events.lines().filter(|line| *line == "session").count(), 3);
    assert!(events.contains("auth:alternate\nsession"));
    // Only the requested reply was generated; the spare session is empty.
    assert_eq!(
        events
            .lines()
            .filter(|line| line.starts_with("prompt:"))
            .count(),
        1
    );
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn acp_failed_preparation_is_retried_on_demand_without_an_extra_prompt() {
    let temp = tempfile::tempdir().unwrap();
    let (config, marker) = acp_config(&temp);
    let server = Server::start(config).await.unwrap();
    agent_request(
        &server,
        "POST",
        "/api/ai/agents/synthetic/connect",
        json!({"method":"synthetic-login"}),
    )
    .await;
    assert_eq!(
        agent_request(
            &server,
            "PUT",
            "/api/ai/routes/assignments",
            json!({"reply":"acp:synthetic"})
        )
        .await
        .0,
        200
    );
    std::fs::write(marker.with_extension("fail-prepare"), b"fail once").unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    let target = manual_target(&mut ws).await;
    for generation in ["before-failure", "after-failure"] {
        send(&mut ws, json!({"type":"generate_reply","generation_id":generation,"target_utterance_id":target})).await;
        let timing = until(&mut ws, |v| {
            v["type"] == "reply_timing" && v["generation_id"] == generation
        })
        .await;
        assert_eq!(timing["outcome"], "completed");
    }
    let events = std::fs::read_to_string(&marker).unwrap();
    assert!(events.contains("prepare-failed"));
    assert_eq!(events.lines().filter(|line| *line == "start").count(), 1);
    assert_eq!(
        events
            .lines()
            .filter(|line| line.starts_with("prompt:"))
            .count(),
        2
    );
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn acp_thought_level_survives_restart_and_follows_model_capabilities() {
    let temp = tempfile::tempdir().unwrap();
    let (config, marker) = acp_config(&temp);
    let server = Server::start(config.clone()).await.unwrap();
    let manifest_path = temp.path().join("data/agents/installed.json");
    let saved =
        || serde_json::from_slice::<Value>(&std::fs::read(&manifest_path).unwrap()).unwrap();
    assert_eq!(
        agent_request(
            &server,
            "POST",
            "/api/ai/agents/synthetic/connect",
            json!({"method":"synthetic-login"})
        )
        .await
        .0,
        200
    );
    let (status, body) = agent_request(
        &server,
        "PUT",
        "/api/ai/agents/synthetic/thought-level",
        json!({"thought_level":"instant"}),
    )
    .await;
    assert_eq!(status, 200);
    assert_eq!(body["thought_level"]["current"], "instant");
    assert_eq!(saved()["synthetic"]["thought_level"], "instant");
    // The client accepts opaque agent values, not a hard-coded list of Codex levels.
    assert_eq!(
        agent_request(
            &server,
            "PUT",
            "/api/ai/agents/synthetic/thought-level",
            json!({"thought_level":"low"})
        )
        .await
        .0,
        422
    );
    assert_eq!(saved()["synthetic"]["thought_level"], "instant");
    let (status, body) = agent_request(
        &server,
        "PUT",
        "/api/ai/agents/synthetic/model",
        json!({"model":"synthetic-accurate"}),
    )
    .await;
    assert_eq!(status, 200);
    assert_eq!(body["thought_level"]["current"], "deliberate");
    assert_eq!(body["thought_level"]["options"][0]["id"], "brief");
    assert!(saved()["synthetic"]["thought_level"].is_null());
    assert_eq!(
        agent_request(
            &server,
            "PUT",
            "/api/ai/agents/synthetic/thought-level",
            json!({"thought_level":"brief"})
        )
        .await
        .0,
        200
    );
    server.shutdown().await.unwrap();

    let server = Server::start(config).await.unwrap();
    let (status, body) = agent_request(
        &server,
        "POST",
        "/api/ai/agents/synthetic/connect",
        json!({"method":"synthetic-login"}),
    )
    .await;
    assert_eq!(status, 200);
    assert_eq!(body["model"]["current"], "synthetic-accurate");
    assert_eq!(body["thought_level"]["current"], "brief");
    let (status, body) = agent_request(
        &server,
        "PUT",
        "/api/ai/agents/synthetic/model",
        json!({"model":"synthetic-fixed"}),
    )
    .await;
    assert_eq!(status, 200);
    assert!(body["thought_level"].is_null());
    assert!(saved()["synthetic"]["thought_level"].is_null());
    assert_eq!(
        agent_request(
            &server,
            "PUT",
            "/api/ai/agents/synthetic/thought-level",
            json!({"thought_level":"brief"})
        )
        .await
        .0,
        422
    );
    assert!(!std::fs::read_to_string(marker)
        .unwrap()
        .contains("invalid-config"));
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn acp_reauthentication_replaces_idle_session_and_failed_auth_stays_unready() {
    let temp = tempfile::tempdir().unwrap();
    let (config, marker) = acp_config(&temp);
    let server = Server::start(config).await.unwrap();
    for (method, ready) in [
        (Some("synthetic-login"), true),
        (Some("synthetic-alternate"), true),
        (Some("synthetic-denied"), false),
        (None, false),
        (Some("synthetic-login"), true),
    ] {
        let (status, body) = agent_request(
            &server,
            "POST",
            "/api/ai/agents/synthetic/connect",
            method.map_or_else(|| json!({}), |method| json!({"method":method})),
        )
        .await;
        assert_eq!(status, 200);
        assert_eq!(body["ready"], ready);
        assert_eq!(body["auth_methods"].as_array().unwrap().len(), 3);
    }
    let events = std::fs::read_to_string(marker).unwrap();
    assert_eq!(
        events.lines().collect::<Vec<_>>(),
        vec![
            "start",
            "auth:primary",
            "session",
            "close",
            "auth:alternate",
            "session",
            "close",
            "auth:denied",
            "auth:primary",
            "session",
        ]
    );
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn acp_cancel_discards_partial_reply_and_keeps_recording_functional() {
    let temp = tempfile::tempdir().unwrap();
    let (config, _) = acp_config(&temp);
    let server = Server::start(config).await.unwrap();
    agent_request(
        &server,
        "POST",
        "/api/ai/agents/synthetic/connect",
        json!({"method":"synthetic-login"}),
    )
    .await;
    assert_eq!(
        agent_request(
            &server,
            "PUT",
            "/api/ai/routes/assignments",
            json!({"reply":"acp:synthetic"})
        )
        .await
        .0,
        200
    );
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    send(
        &mut ws,
        json!({"type":"manual_speech","text":"SLOW synthetic"}),
    )
    .await;
    let target = until(&mut ws, |v| v["type"] == "stt_final").await["utterance_id"].clone();
    send(
        &mut ws,
        json!({"type":"generate_reply","generation_id":"cancel-acp","target_utterance_id":target}),
    )
    .await;
    until(&mut ws, |v| v["type"] == "reply_chunk").await;
    send(
        &mut ws,
        json!({"type":"cancel_reply","generation_id":"cancel-acp","target_utterance_id":target}),
    )
    .await;
    assert_eq!(
        until(&mut ws, |v| v["type"] == "reply_cancel_result").await["status"],
        "applied"
    );
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    assert!(saved_suggestions(&server).await.is_empty());
    drop(ws);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn acp_errors_permissions_and_truncated_turns_are_not_saved() {
    for scenario in ["FAIL", "TOOL", "TRUNCATED"] {
        let temp = tempfile::tempdir().unwrap();
        let (config, _) = acp_config(&temp);
        let server = Server::start(config).await.unwrap();
        assert_eq!(
            agent_request(
                &server,
                "POST",
                "/api/ai/agents/synthetic/connect",
                json!({"method":"synthetic-login"})
            )
            .await
            .0,
            200
        );
        assert_eq!(
            agent_request(
                &server,
                "PUT",
                "/api/ai/routes/assignments",
                json!({"reply":"acp:synthetic"})
            )
            .await
            .0,
            200
        );
        let mut ws = connect(&server).await;
        start(&mut ws).await;
        send(&mut ws, json!({"type":"manual_speech","text":scenario})).await;
        let target = until(&mut ws, |v| v["type"] == "stt_final").await["utterance_id"].clone();
        send(&mut ws, json!({"type":"generate_reply","generation_id":"failed-acp","target_utterance_id":target})).await;
        let error = until(&mut ws, |v| v["type"] == "suggestion_error").await;
        assert!(!error.to_string().contains("synthetic secret"));
        if scenario == "TOOL" {
            assert!(error["text"].as_str().unwrap().contains("外部操作"));
        }
        send(&mut ws, json!({"type":"stop_meeting"})).await;
        assert_eq!(
            until(&mut ws, |v| v["type"] == "meeting_state"
                && v["running"] == false)
            .await["saved"],
            true
        );
        assert!(saved_suggestions(&server).await.is_empty());
        drop(ws);
        server.shutdown().await.unwrap();
    }
}

#[tokio::test]
async fn startup_reconciles_interrupted_meetings_and_preserves_usable_recordings() {
    use meeting_storage::models::Status;
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "model");
    std::fs::create_dir_all(&settings.data_dir).unwrap();
    seed_retention(&settings, "interrupted", Status::Active, None, 0).await;
    seed_retention(
        &settings,
        "completed",
        Status::Completed,
        Some("2025-01-01T01:00:00Z"),
        0,
    )
    .await;
    let directory = settings.data_dir.join("recordings/interrupted");
    std::fs::create_dir_all(&directory).unwrap();
    let path = directory.join("self.wav");
    let mut writer = hound::WavWriter::create(
        &path,
        hound::WavSpec {
            channels: 1,
            sample_rate: 16000,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        },
    )
    .unwrap();
    for _ in 0..640 {
        writer.write_sample(1_i16).unwrap();
    }
    writer.finalize().unwrap();
    let original = std::fs::read(&path).unwrap();
    let broken = directory.join("other.wav");
    std::fs::write(&broken, b"synthetic incomplete recording").unwrap();
    for _ in 0..2 {
        let server = Server::start(settings.clone()).await.unwrap();
        let (_, body) = http(&server, "GET", "/meetings/interrupted", "").await;
        let detail: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(detail["status"], "aborted");
        assert!(detail["ended_at"].is_null());
        assert!(detail["duration_seconds"].is_null());
        assert_eq!(detail["recording_assets"].as_array().unwrap().len(), 1);
        let (status, audio) =
            http(&server, "GET", "/meetings/interrupted/recordings/self", "").await;
        assert_eq!(status, 200);
        assert_eq!(audio, original);
        assert_eq!(
            std::fs::read(&broken).unwrap(),
            b"synthetic incomplete recording"
        );
        let (_, body) = http(&server, "GET", "/meetings/completed", "").await;
        assert_eq!(
            serde_json::from_slice::<Value>(&body).unwrap()["status"],
            "completed"
        );
        server.shutdown().await.unwrap();
    }
    let server = Server::start(settings).await.unwrap();
    assert_eq!(
        http(&server, "DELETE", "/meetings/interrupted", "").await.0,
        200
    );
    assert!(!directory.exists());
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn another_runtime_cannot_reconcile_a_live_meeting() {
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "model");
    let server = Server::start(settings.clone()).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    assert!(matches!(
        Server::start(settings).await,
        Err(meeting_desktop_runtime::Error::AlreadyRunning)
    ));
    let (_, body) = http(&server, "GET", "/meetings", "").await;
    let page: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(page["items"][0]["status"], "active");
    drop(ws);
    server.shutdown().await.unwrap();
}

#[cfg(unix)]
#[tokio::test]
async fn interrupted_recordings_do_not_follow_symlinks() {
    use meeting_storage::models::Status;
    let temp = tempfile::tempdir().unwrap();
    let settings = config(&temp, "model");
    std::fs::create_dir_all(&settings.data_dir).unwrap();
    seed_retention(&settings, "linked", Status::Active, None, 0).await;
    let outside = temp.path().join("outside.wav");
    let mut writer = hound::WavWriter::create(
        &outside,
        hound::WavSpec {
            channels: 1,
            sample_rate: 16000,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        },
    )
    .unwrap();
    writer.write_sample(1_i16).unwrap();
    writer.finalize().unwrap();
    let original = std::fs::read(&outside).unwrap();
    let directory = settings.data_dir.join("recordings/linked");
    std::fs::create_dir_all(&directory).unwrap();
    std::os::unix::fs::symlink(&outside, directory.join("self.wav")).unwrap();
    let server = Server::start(settings).await.unwrap();
    let (_, body) = http(&server, "GET", "/meetings/linked", "").await;
    let detail: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(detail["status"], "aborted");
    assert_eq!(detail["recording_assets"], json!([]));
    assert_eq!(http(&server, "DELETE", "/meetings/linked", "").await.0, 200);
    assert_eq!(std::fs::read(outside).unwrap(), original);
    server.shutdown().await.unwrap();
}

#[tokio::test]
async fn recording_finalization_error_preserves_both_files_and_partial_history() {
    let temp = tempfile::tempdir().unwrap();
    let mut settings = config(&temp, "model");
    let worker = temp.path().join(format!(
        "recording-failure-worker{}",
        std::env::consts::EXE_SUFFIX
    ));
    std::fs::copy(&settings.audio_worker, &worker).unwrap();
    settings.audio_worker = worker;
    let server = Server::start(settings.clone()).await.unwrap();
    let mut ws = connect(&server).await;
    start(&mut ws).await;
    send(&mut ws, json!({"type":"stop_meeting"})).await;
    let stopped = until(&mut ws, |v| {
        v["type"] == "meeting_state" && v["running"] == false
    })
    .await;
    assert_eq!(stopped["end_status"], "interrupted");
    let (_, body) = http(&server, "GET", "/meetings", "").await;
    let page: Value = serde_json::from_slice(&body).unwrap();
    let id = page["items"][0]["id"].as_str().unwrap();
    assert_eq!(page["items"][0]["status"], "aborted");
    let (_, body) = http(&server, "GET", &format!("/meetings/{id}"), "").await;
    let detail: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(detail["turns"].as_array().unwrap().len(), 2);
    assert_eq!(detail["recording_assets"].as_array().unwrap().len(), 2);
    for role in ["self", "other"] {
        assert_eq!(
            http(
                &server,
                "GET",
                &format!("/meetings/{id}/recordings/{role}"),
                ""
            )
            .await
            .0,
            200
        );
        assert!(settings
            .data_dir
            .join(format!("recordings/{id}/{role}.wav"))
            .is_file());
    }
    server.shutdown().await.unwrap();
}
