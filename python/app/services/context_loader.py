from pathlib import Path


def ensure_default_context_directory(*, context_dir: Path, user_data_dir: Path) -> None:
    """Create the application-owned context directory without masking invalid overrides."""
    default_context_dir = user_data_dir / "context"
    if context_dir == default_context_dir:
        default_context_dir.mkdir(parents=True, exist_ok=True)


def load_context_files(context_dir: Path) -> str:
    """Read all .md files in context_dir and concatenate them."""
    if not context_dir.exists():
        return ""
    parts: list[str] = []
    for md_file in sorted(context_dir.glob("*.md")):
        text = md_file.read_text(encoding="utf-8").strip()
        if text:
            parts.append(f"## {md_file.stem}\n{text}")
    if not parts:
        return ""
    return "\n\n".join(parts)
