from pathlib import Path

from app.services.context_loader import ensure_default_context_directory


def test_creates_application_owned_context_directory(tmp_path: Path) -> None:
    context_dir = tmp_path / "context"

    ensure_default_context_directory(
        context_dir=context_dir,
        user_data_dir=tmp_path,
    )

    assert context_dir.is_dir()


def test_does_not_create_missing_override(tmp_path: Path) -> None:
    context_dir = tmp_path / "external" / "context"

    ensure_default_context_directory(
        context_dir=context_dir,
        user_data_dir=tmp_path,
    )

    assert not context_dir.exists()
