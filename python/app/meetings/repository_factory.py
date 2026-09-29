"""Select a single SQLite owner for the existing meeting workflow."""

import logging
import os
from pathlib import Path

from app.meetings.repository import MeetingHistoryRepository

logger = logging.getLogger(__name__)


def build_history_repository(db_path: str | Path) -> MeetingHistoryRepository:
    runtime = os.environ.get("MEETING_STORAGE_RUNTIME", "python")
    if runtime == "rust":
        from app.meetings.native_repository import NativeMeetingHistoryRepository

        logger.info("Meeting storage: Rust / SQLx")
        return NativeMeetingHistoryRepository(db_path)
    if runtime == "python":
        from app.meetings.sqlite_repository import SqliteMeetingHistoryRepository

        logger.info("Meeting storage: Python / sqlite3")
        return SqliteMeetingHistoryRepository(db_path)
    raise ValueError("MEETING_STORAGE_RUNTIME must be python or rust")
