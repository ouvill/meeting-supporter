"""Value types shared by the Codex app-server boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from app.agents.codex_installation import CodexInstallation


class ProcessState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    CLOSED = "closed"


class AuthState(StrEnum):
    UNKNOWN = "unknown"
    UNAUTHENTICATED = "unauthenticated"
    LOGGING_IN = "logging_in"
    AUTHENTICATED = "authenticated"


class TurnState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    ACTIVE = "active"
    INTERRUPTING = "interrupting"


@dataclass(frozen=True)
class CodexAccountSnapshot:
    authenticated: bool
    account_type: str | None
    plan_type: str | None
    requires_openai_auth: bool


@dataclass(frozen=True)
class CodexStatusSnapshot:
    installation: CodexInstallation
    process_state: ProcessState
    auth_state: AuthState
    turn_state: TurnState
    account: CodexAccountSnapshot | None


@dataclass(frozen=True)
class CodexModelSelection:
    requested_model: str
    effective_model: str
    effective_model_provider: str
    reasoning_effort: Literal["low"]
    service_tier: Literal["standard", "priority"]
    requested_service_tier: Literal["priority"] | None
    effective_service_tier: str | None


@dataclass(frozen=True)
class CodexServiceTierStatus:
    advertised: Literal["standard", "priority"]
    effective: Literal["standard", "priority"] | None


class CodexSafeError(RuntimeError):
    """Error whose fields are safe to return across the API boundary."""

    code: str
    message: str
    retryable: bool

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
