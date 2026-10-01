//! Offline contract export: no runtime, database, model or secret-store initialization.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args_os()
        .nth(1)
        .ok_or("usage: export-openapi OUTPUT")?;
    let schema: serde_json::Value = serde_json::from_str(&meeting_desktop_runtime::openapi())?;
    std::fs::write(
        path,
        format!("{}\n", serde_json::to_string_pretty(&schema)?),
    )?;
    Ok(())
}
