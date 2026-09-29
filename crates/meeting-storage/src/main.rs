//! Transitional IPC adapter; Repository itself is reusable from Tauri.
use meeting_storage::{models::Command, Repository};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    io::{self, BufRead, Read, Write},
    path::Path,
};
const MAX_REQUEST: usize = 8 * 1024 * 1024;
const MAX_RESPONSE: usize = 32 * 1024 * 1024;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    id: u64,
    command: Command,
}
fn send(writer: &mut impl Write, value: Value) -> io::Result<()> {
    let data = serde_json::to_vec(&value)?;
    if data.len() >= MAX_RESPONSE {
        return Err(io::Error::other("response too large"));
    }
    writer.write_all(&data)?;
    writer.write_all(b"\n")?;
    writer.flush()
}
async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 2 || args[0] != "--database" {
        return Err("invalid arguments".into());
    }
    let mut output = io::stdout().lock();
    let repository = match Repository::open(Path::new(&args[1])).await {
        Ok(repository) => repository,
        Err(code) => {
            send(&mut output, json!({"id":null,"type":"error","code":code}))?;
            return Err("storage unavailable".into());
        }
    };
    send(
        &mut output,
        json!({"id":null,"type":"ready","protocol":1,"schema":2}),
    )?;
    let mut input = io::stdin().lock();
    loop {
        let mut line = Vec::new();
        let count = input
            .by_ref()
            .take((MAX_REQUEST + 1) as u64)
            .read_until(b'\n', &mut line)?;
        if count == 0 {
            break;
        }
        if count > MAX_REQUEST || !line.ends_with(b"\n") {
            return Err("invalid request size".into());
        }
        let request: Request = serde_json::from_slice(&line).map_err(|_| "invalid request")?;
        let result = match repository.execute(request.command).await {
            Ok(value) => json!({"id":request.id,"type":"result","value":value}),
            Err(code) => json!({"id":request.id,"type":"error","code":code}),
        };
        send(&mut output, result)?;
    }
    repository.close().await;
    Ok(())
}
#[tokio::main(flavor = "current_thread")]
async fn main() {
    if run().await.is_err() {
        eprintln!("storage worker failed");
        std::process::exit(1);
    }
}
