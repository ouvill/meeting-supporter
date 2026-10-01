//! Read the existing usage log without inventing a zero balance during migration.
use crate::Error;
use chrono::Datelike;
use serde::{Deserialize, Serialize};
use std::{
    io::{BufRead, BufReader, Read},
    path::Path,
};
#[derive(Default, Serialize)]
pub struct Summary {
    input_tokens: u64,
    output_tokens: u64,
    pub estimated_cost_jpy: f64,
    pub incomplete_requests: u64,
    request_count: u64,
}
#[derive(Deserialize)]
struct Record {
    ts: String,
    #[serde(default)]
    model: String,
    #[serde(default)]
    input_tokens: u64,
    #[serde(default)]
    output_tokens: u64,
    estimated_cost_jpy: Option<f64>,
    request_id: Option<String>,
    meeting_id: Option<String>,
    #[serde(default)]
    incomplete: bool,
}
pub fn month(path: &Path) -> Result<Summary, Error> {
    summary(path, None)
}
pub fn meeting(path: &Path, meeting_id: &str) -> Result<Summary, Error> {
    summary(path, Some(meeting_id))
}
fn summary(path: &Path, meeting_id: Option<&str>) -> Result<Summary, Error> {
    let file = match std::fs::File::open(path) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Summary::default()),
        Err(e) => return Err(e.into()),
    };
    let mut reader = BufReader::new(file);
    let now = chrono::Utc::now();
    let mut summary = Summary::default();
    let mut line = Vec::new();
    let mut records = Vec::new();
    let mut requests = std::collections::HashMap::<String, Record>::new();
    loop {
        line.clear();
        let count = reader.by_ref().take(65537).read_until(b'\n', &mut line)?;
        if count == 0 {
            break;
        }
        if count > 65536 {
            return Err(Error::Settings);
        }
        let Ok(record) = serde_json::from_slice::<Record>(&line) else {
            continue;
        };
        if let Some(id) = record.request_id.clone() {
            requests.insert(id, record);
        } else {
            records.push(record);
        }
    }
    records.extend(requests.into_values());
    for record in records {
        let Ok(ts) = chrono::DateTime::parse_from_rfc3339(&record.ts) else {
            continue;
        };
        if let Some(id) = meeting_id {
            if record.meeting_id.as_deref() != Some(id) {
                continue;
            }
        } else if ts.year() != now.year() || ts.month() != now.month() {
            continue;
        }
        if record.incomplete {
            summary.incomplete_requests += 1;
        }
        let cost = record
            .estimated_cost_jpy
            .unwrap_or_else(|| estimate(&record));
        if !cost.is_finite() || cost < 0.0 {
            continue;
        }
        summary.input_tokens = summary
            .input_tokens
            .checked_add(record.input_tokens)
            .ok_or(Error::Settings)?;
        summary.output_tokens = summary
            .output_tokens
            .checked_add(record.output_tokens)
            .ok_or(Error::Settings)?;
        summary.request_count += 1;
        summary.estimated_cost_jpy += cost;
    }
    if !summary.estimated_cost_jpy.is_finite() {
        return Err(Error::Settings);
    }
    summary.estimated_cost_jpy = (summary.estimated_cost_jpy * 1_000_000.0).round() / 1_000_000.0;
    Ok(summary)
}
fn estimate(record: &Record) -> f64 {
    let model = record
        .model
        .rsplit('/')
        .next()
        .unwrap_or_default()
        .rsplit(':')
        .next()
        .unwrap_or_default();
    let (input, output) = match model {
        "gemini-3.1-flash-lite" => (12.0, 48.0),
        "gemini-3-flash-preview" | "gemini-2.5-flash-lite" => (16.0, 64.0),
        "gemini-2.5-flash" | "claude-haiku-4-5-20251001" => (
            48.0,
            if model == "gemini-2.5-flash" {
                192.0
            } else {
                240.0
            },
        ),
        "gpt-5.4-nano" => (8.0, 32.0),
        "gpt-5.4-mini" => (32.0, 128.0),
        _ => (0.0, 0.0),
    };
    (record.input_tokens as f64 * input + record.output_tokens as f64 * output).round()
        / 1_000_000.0
}
static JOURNAL: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

pub(crate) async fn begin(
    budget: &crate::settings::UsageBudget,
    directory: &Path,
    meeting_id: &str,
    request_id: &str,
    model: &str,
    local: bool,
) -> Result<(), Error> {
    let _guard = JOURNAL.lock().await;
    let path = directory.join("usage.jsonl");
    let meeting_id = meeting_id.to_owned();
    let request_id = request_id.to_owned();
    let model = model.to_owned();
    let budget = budget.clone();
    tokio::task::spawn_blocking(move || {
        if !local {
            for (limit, current) in [
                (budget.meeting_limit_jpy, meeting(&path, &meeting_id)?),
                (budget.monthly_limit_jpy, month(&path)?),
            ] {
                if limit > 0.0 && (current.incomplete_requests > 0 || current.estimated_cost_jpy >= limit || !known_price(&model)) {
                    return Err(crate::ai::AiError::Budget.into());
                }
            }
        }
        append(&path, &serde_json::json!({"version":2,"ts":chrono::Utc::now().to_rfc3339(),"request_id":request_id,"meeting_id":meeting_id,"model":model,"incomplete":!local,"estimated_cost_jpy":0.0}))
    }).await.map_err(|_| Error::Closed)?
}
fn known_price(model: &str) -> bool {
    matches!(
        model,
        "gemini-3.1-flash-lite"
            | "gemini-3-flash-preview"
            | "gemini-2.5-flash-lite"
            | "gemini-2.5-flash"
            | "claude-haiku-4-5-20251001"
            | "gpt-5.4-nano"
            | "gpt-5.4-mini"
    )
}
pub async fn finish(
    directory: &Path,
    meeting_id: &str,
    request_id: &str,
    model: &str,
    usage: &rig::completion::Usage,
    local: bool,
) -> Result<(), Error> {
    let _guard = JOURNAL.lock().await;
    let path = directory.join("usage.jsonl");
    let mut record = serde_json::json!({"version":2,"ts":chrono::Utc::now().to_rfc3339(),"request_id":request_id,"meeting_id":meeting_id,"model":model,"input_tokens":usage.input_tokens,"output_tokens":usage.output_tokens,"incomplete": !local && (!known_price(model) || usage.total_tokens==0)});
    let cost = if local {
        0.0
    } else {
        estimate(&serde_json::from_value::<Record>(record.clone()).map_err(|_| Error::Settings)?)
    };
    record["estimated_cost_jpy"] = serde_json::json!(cost);
    tokio::task::spawn_blocking(move || append(&path, &record))
        .await
        .map_err(|_| Error::Closed)?
}
fn append(path: &Path, record: &serde_json::Value) -> Result<(), Error> {
    use std::io::Write;
    let mut options = std::fs::OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    let mut bytes = serde_json::to_vec(record)?;
    bytes.push(b'\n');
    file.write_all(&bytes)?;
    file.sync_data()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn request_journal_preserves_unknown_usage_and_enforces_budgets() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("usage.jsonl");
        let budget = crate::settings::UsageBudget {
            monthly_limit_jpy: 100.0,
            meeting_limit_jpy: 1.0,
        };
        begin(
            &budget,
            temp.path(),
            "synthetic-meeting",
            "request-1",
            "gpt-5.4-mini",
            false,
        )
        .await
        .unwrap();
        assert_eq!(month(&path).unwrap().incomplete_requests, 1);
        assert!(matches!(
            begin(
                &budget,
                temp.path(),
                "synthetic-meeting",
                "request-2",
                "gpt-5.4-mini",
                false
            )
            .await,
            Err(Error::Ai(crate::ai::AiError::Budget))
        ));
        let mut usage = rig::completion::Usage::new();
        usage.input_tokens = 1_000_000;
        usage.total_tokens = 1_000_000;
        finish(
            temp.path(),
            "synthetic-meeting",
            "request-1",
            "gpt-5.4-mini",
            &usage,
            false,
        )
        .await
        .unwrap();
        let summary = meeting(&path, "synthetic-meeting").unwrap();
        assert_eq!(summary.request_count, 1);
        assert_eq!(summary.incomplete_requests, 0);
        assert_eq!(summary.estimated_cost_jpy, 32.0);
        assert_eq!(meeting(&path, "other-meeting").unwrap().request_count, 0);
        assert!(matches!(
            begin(
                &budget,
                temp.path(),
                "synthetic-meeting",
                "request-3",
                "gpt-5.4-mini",
                false
            )
            .await,
            Err(Error::Ai(crate::ai::AiError::Budget))
        ));
        // Local inference still works when cloud spending is blocked.
        begin(
            &budget,
            temp.path(),
            "synthetic-meeting",
            "local",
            "synthetic-model",
            true,
        )
        .await
        .unwrap();
        assert!(std::fs::read_to_string(path)
            .unwrap()
            .lines()
            .all(|line| !line.contains("prompt")));
    }
    #[test]
    fn existing_monthly_usage_and_legacy_pricing_are_preserved() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("usage.jsonl");
        let now = chrono::Utc::now().to_rfc3339();
        std::fs::write(&path,format!("{{\"ts\":\"{now}\",\"input_tokens\":100,\"estimated_cost_jpy\":1.25}}\n{{\"ts\":\"{now}\",\"input_tokens\":1000000,\"model\":\"gpt-5.4-nano\"}}\n{{\"ts\":\"2000-01-01T00:00:00Z\",\"estimated_cost_jpy\":9000}}\nmalformed\n")).unwrap();
        let summary = month(&path).unwrap();
        assert_eq!(summary.request_count, 2);
        assert_eq!(summary.estimated_cost_jpy, 9.25);
        assert_eq!(summary.input_tokens, 1_000_100);
    }
}
