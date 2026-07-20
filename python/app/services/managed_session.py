"""Process-only managed-service session state.

Tokens enter only through the capability-protected loopback endpoint and are
never persisted, logged, or returned to the browser-facing API.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import final


@final
@dataclass(frozen=True)
class ManagedSession:
    access_token: str
    expires_at: int
    api_base_url: str


@final
class ManagedSessionStore:
    def __init__(self, capability: str) -> None:
        if len(capability) < 32:
            raise ValueError("managed session capability is invalid")
        self._capability = capability
        self._session: ManagedSession | None = None
        self._lock = RLock()

    def authorize(self, capability: str | None) -> bool:
        return capability is not None and capability == self._capability

    def replace(self, session: ManagedSession) -> None:
        if not session.access_token or session.expires_at <= 0 or not session.api_base_url.startswith("https://"):
            raise ValueError("managed session is invalid")
        with self._lock:
            self._session = session

    def clear(self) -> None:
        with self._lock:
            self._session = None

    def get(self) -> ManagedSession | None:
        with self._lock:
            return self._session


__all__ = ["ManagedSession", "ManagedSessionStore"]
