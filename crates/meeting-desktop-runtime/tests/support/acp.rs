//! Synthetic ACP peer. Never contacts a service or reads user configuration.
use serde_json::{json, Value};
use std::{
    io::{BufRead, Write},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
};
fn emit(output: &Mutex<std::io::Stdout>, value: Value) {
    let mut output = output.lock().unwrap();
    writeln!(output, "{value}").unwrap();
    output.flush().unwrap();
}
fn main() {
    let marker = std::env::args().nth(1).unwrap();
    let mut log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&marker)
        .unwrap();
    writeln!(log, "start").unwrap();
    let output = Arc::new(Mutex::new(std::io::stdout()));
    let authenticated = AtomicBool::new(false);
    let cancelled = Arc::new(AtomicBool::new(false));
    let mut sessions = 0;
    for line in std::io::stdin().lock().lines() {
        let value: Value = serde_json::from_str(&line.unwrap()).unwrap();
        if value["method"].is_null() {
            continue;
        }
        let id = &value["id"];
        let params = &value["params"];
        let result = match value["method"].as_str().unwrap_or("") {
            "initialize" => {
                json!({"protocolVersion":1,"agentCapabilities":{"sessionCapabilities":{"close":{}}},"authMethods":[{"id":"synthetic-login","name":"テスト認証"},{"id":"synthetic-alternate","name":"別のテスト認証"},{"id":"synthetic-denied","name":"失敗するテスト認証"}]})
            }
            "authenticate" => {
                if params["methodId"] == "synthetic-denied" {
                    authenticated.store(false, Ordering::SeqCst);
                    writeln!(log, "auth:denied").unwrap();
                    emit(
                        &output,
                        json!({"jsonrpc":"2.0","id":id,"error":{"code":-32000,"message":"synthetic auth failure"}}),
                    );
                    continue;
                }
                writeln!(
                    log,
                    "auth:{}",
                    if params["methodId"] == "synthetic-alternate" {
                        "alternate"
                    } else {
                        "primary"
                    }
                )
                .unwrap();
                authenticated.store(true, Ordering::SeqCst);
                json!({})
            }
            "session/new" if !authenticated.load(Ordering::SeqCst) => {
                emit(
                    &output,
                    json!({"jsonrpc":"2.0","id":id,"error":{"code":-32000,"message":"synthetic secret must not surface"}}),
                );
                continue;
            }
            "session/new" => {
                sessions += 1;
                writeln!(log, "session").unwrap();
                json!({"sessionId":format!("session-{sessions}"),"configOptions":[{
                    "id":"model","name":"Model","category":"model","type":"select",
                    "currentValue":"synthetic-fast","options":[
                        {"value":"synthetic-fast","name":"Synthetic Fast"},
                        {"value":"synthetic-accurate","name":"Synthetic Accurate"}
                    ]
                }]})
            }
            "session/set_config_option" => {
                let requested = params["value"].as_str().unwrap_or("");
                if !matches!(requested, "synthetic-fast" | "synthetic-accurate") {
                    emit(
                        &output,
                        json!({"jsonrpc":"2.0","id":id,"error":{"code":-32602,"message":"unknown model"}}),
                    );
                    continue;
                }
                writeln!(log, "model:{requested}").unwrap();
                json!({"configOptions":[{
                    "id":"model","name":"Model","category":"model","type":"select",
                    "currentValue":requested,"options":[
                        {"value":"synthetic-fast","name":"Synthetic Fast"},
                        {"value":"synthetic-accurate","name":"Synthetic Accurate"}
                    ]
                }]})
            }
            "session/close" => {
                writeln!(log, "close").unwrap();
                json!({})
            }
            "session/prompt" => {
                cancelled.store(false, Ordering::SeqCst);
                let output = output.clone();
                let cancelled = cancelled.clone();
                let session = params["sessionId"].clone();
                let id = id.clone();
                let prompt = params["prompt"].to_string();
                std::thread::spawn(move || {
                    emit(
                        &output,
                        json!({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"unrelated","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"wrong session"}}}}),
                    );
                    emit(
                        &output,
                        json!({"jsonrpc":"2.0","id":"stale-permission","method":"session/request_permission","params":{"sessionId":"unrelated","toolCall":{"toolCallId":"stale-tool","title":"Synthetic stale tool","status":"pending"},"options":[{"optionId":"allow","name":"Allow","kind":"allow_once"}]}}),
                    );
                    emit(
                        &output,
                        json!({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":session,"update":{"sessionUpdate":"agent_thought_chunk","content":{"type":"text","text":"private reasoning"}}}}),
                    );
                    emit(
                        &output,
                        json!({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":session,"update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"合成の返答です。"}}}}),
                    );
                    if prompt.contains("FAIL") {
                        emit(
                            &output,
                            json!({"jsonrpc":"2.0","id":id,"error":{"code":-32603,"message":"synthetic secret must not surface"}}),
                        );
                        return;
                    }
                    if prompt.contains("TOOL") {
                        emit(
                            &output,
                            json!({"jsonrpc":"2.0","id":"permission-1","method":"session/request_permission","params":{"sessionId":session,"toolCall":{"toolCallId":"tool-1","title":"Synthetic tool","status":"pending"},"options":[{"optionId":"allow","name":"Allow","kind":"allow_once"}]}}),
                        );
                        std::thread::sleep(std::time::Duration::from_millis(200));
                    }
                    if prompt.contains("SLOW") {
                        for _ in 0..100 {
                            if cancelled.load(Ordering::SeqCst) {
                                break;
                            }
                            std::thread::sleep(std::time::Duration::from_millis(30));
                        }
                    }
                    emit(
                        &output,
                        json!({"jsonrpc":"2.0","id":id,"result":{"stopReason":if cancelled.load(Ordering::SeqCst){"cancelled"}else if prompt.contains("TRUNCATED"){"max_tokens"}else{"end_turn"}}}),
                    );
                });
                continue;
            }
            "session/cancel" => {
                cancelled.store(true, Ordering::SeqCst);
                writeln!(log, "cancel").unwrap();
                continue;
            }
            _ if id.is_null() => continue,
            _ => {
                emit(
                    &output,
                    json!({"jsonrpc":"2.0","id":id,"error":{"code":-32601,"message":"unsupported"}}),
                );
                continue;
            }
        };
        emit(&output, json!({"jsonrpc":"2.0","id":id,"result":result}));
    }
}
