"""Select the lifecycle owner while preserving the existing UI entry points."""

import logging
import os

from app.meetings.lifecycle import MeetingLifecycleCoordinator

logger = logging.getLogger(__name__)


def lifecycle_coordinator_type() -> type[MeetingLifecycleCoordinator]:
    runtime = os.environ.get("MEETING_SESSION_RUNTIME", "python")
    if runtime == "python":
        return MeetingLifecycleCoordinator
    if runtime == "rust":
        from app.meetings.native_lifecycle import NativeMeetingLifecycleCoordinator

        logger.info("Meeting lifecycle: Rust")
        return NativeMeetingLifecycleCoordinator
    raise ValueError("MEETING_SESSION_RUNTIME must be python or rust")
