//! Temporary adapter for the existing Python effect executor.
use meeting_session::{Command, Coordinator};
use serde::Deserialize;
use serde_json::json;
use std::io::{self, BufRead, Read, Write};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    id: u64,
    command: Command,
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut coordinator = Coordinator::default();
    let mut output = io::stdout().lock();
    writeln!(output, "{}", json!({"id":null,"type":"ready","protocol":1}))?;
    output.flush()?;
    let mut input = io::stdin().lock();
    loop {
        let mut line = Vec::new();
        let count = input.by_ref().take(4097).read_until(b'\n', &mut line)?;
        if count == 0 {
            return Ok(());
        }
        if count > 4096 || !line.ends_with(b"\n") {
            return Err("invalid request".into());
        }
        let request: Request = serde_json::from_slice(&line).map_err(|_| "invalid request")?;
        let result = match coordinator.execute(request.command) {
            Ok(snapshot) => json!({"id":request.id,"type":"result","snapshot":snapshot}),
            Err(code) => json!({"id":request.id,"type":"error","code":code}),
        };
        writeln!(output, "{result}")?;
        output.flush()?;
    }
}
fn main() {
    if run().is_err() {
        eprintln!("meeting session worker failed");
        std::process::exit(1);
    }
}
