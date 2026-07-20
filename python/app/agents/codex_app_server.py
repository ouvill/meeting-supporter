"""Secure, narrow asyncio peer for the Codex app-server JSONL protocol."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from asyncio.subprocess import PIPE, Process
from collections import deque
from collections.abc import AsyncIterator, Coroutine, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import TypeAdapter, ValidationError

from app.agents.codex_protocol import (
    AccountLoginCompletedNotification,
    AccountReadParams,
    AccountReadResult,
    AgentMessageDeltaNotification,
    ChatGptLoginStartParams,
    ChatGptLoginStartResult,
    ClientInfo,
    CodexProtocolModel,
    DeviceCodeLoginStartParams,
    DeviceCodeLoginStartResult,
    EmptyParams,
    EmptyResult,
    InitializeCapabilities,
    InitializeParams,
    InitializeResult,
    LoginCancelParams,
    LoginCancelResult,
    ModelListItem,
    ModelListParams,
    ModelListResult,
    ModelReroutedNotification,
    RateLimitsReadResult,
    TextUserInput,
    ThreadStartParams,
    ThreadStartResult,
    ThreadUnsubscribeParams,
    ThreadUnsubscribeResult,
    TurnCompletedNotification,
    TurnInterruptParams,
    TurnInterruptResult,
    TurnStartParams,
    TurnStartResult,
)

logger = logging.getLogger(__name__)

_CODEX_VERSION = re.compile(r"^codex-cli\s+(\S+)$")
_RELEASE_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_MINIMUM_CODEX_VERSION: Final = (0, 144, 0)
_SCHEMA_VERIFIED_CODEX_VERSIONS: Final[frozenset[str]] = frozenset({"0.144.0", "0.144.1"})
MINIMUM_CODEX_VERSION_LABEL: Final = "0.144.0 以降"
_REQUEST_TIMEOUT: Final = 15.0
_INITIALIZE_TIMEOUT: Final = 10.0
_TURN_START_TIMEOUT: Final = 30.0
_INTERRUPT_TIMEOUT: Final = 3.0
_STDIO_LIMIT: Final = 1024 * 1024
_STDERR_LIMIT: Final = 64 * 1024
_DELTA_QUEUE_LIMIT: Final = 512
_DELTA_SIZE_LIMIT: Final = 256 * 1024
_MODEL_LIST_PAGE_SIZE: Final = 100
_MODEL_LIST_MAX_PAGES: Final = 16
_REPLY_REASONING_EFFORT: Final[Literal["low"]] = "low"
_STANDARD_SERVICE_TIER: Final[Literal["standard"]] = "standard"
_PRIORITY_SERVICE_TIER: Final[Literal["priority"]] = "priority"
_END: Final = object()
_TURN_NOTIFICATION_METHODS: Final[frozenset[str]] = frozenset(
    {"item/agentMessage/delta", "turn/completed", "model/rerouted"}
)
_REPLY_INSTRUCTIONS: Final = (
    "Return only a direct text reply to the user's request. "
    "Do not inspect files, run commands, call tools, browse, load skills, or delegate to other agents."
)

_CHILD_ENVIRONMENT_VARIABLES: Final[frozenset[str]] = frozenset(
    {
        "ALL_PROXY",
        "CURL_CA_BUNDLE",
        "CODEX_HOME",
        "DBUS_SESSION_BUS_ADDRESS",
        "GNOME_KEYRING_CONTROL",
        "HOME",
        "HOMEPATH",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "LANG",
        "NODE_EXTRA_CA_CERTS",
        "NO_PROXY",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "XDG_RUNTIME_DIR",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)
_JSON_OBJECT: Final[TypeAdapter[dict[str, object]]] = TypeAdapter(dict[str, object])


def _child_environment(parent_environment: Mapping[str, str]) -> dict[str, str]:
    """Pass only authentication, connectivity, locale, and temporary-path inputs to Codex."""

    return {
        key: value
        for key, value in parent_environment.items()
        if key in _CHILD_ENVIRONMENT_VARIABLES or key.startswith("LC_")
    }


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
class CodexInstallation:
    binary: Path | None
    version: str | None
    compatible: bool
    schema_verified: bool
    reason_code: str | None


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


def _parse_release_version(value: str | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    match = _RELEASE_VERSION.fullmatch(value)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _codex_process_command(binary: Path, *arguments: str) -> tuple[str, ...]:
    """Launch native binaries directly, with an interpreter only for Python test peers."""
    if os.name == "nt" and binary.suffix.lower() == ".py":
        return (sys.executable, os.fspath(binary), *arguments)
    return (os.fspath(binary), *arguments)


async def _terminate_version_probe(process: Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    try:
        _ = await asyncio.wait_for(process.wait(), timeout=2.0)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        _ = await process.wait()


async def _reap_version_probe(process: Process) -> None:
    cleanup_task = asyncio.create_task(_terminate_version_probe(process))
    try:
        await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        await cleanup_task
        raise


async def inspect_codex_installation(binary: str | os.PathLike[str] | None = None) -> CodexInstallation:
    """Resolve the official CLI and reject only versions below the protocol baseline."""

    candidate = os.fspath(binary) if binary is not None else os.environ.get("CODEX_BINARY")
    if candidate:
        path = Path(candidate)
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            return CodexInstallation(None, None, False, False, "invalid_binary")
    else:
        resolved = shutil.which("codex")
        if resolved is None:
            return CodexInstallation(None, None, False, False, "not_installed")
        path = Path(resolved).resolve()

    try:
        process = await asyncio.create_subprocess_exec(
            *_codex_process_command(path, "--version"),
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
            limit=4096,
        )
    except OSError:
        return CodexInstallation(path, None, False, False, "version_unavailable")

    try:
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=3.0)
    except TimeoutError:
        await _reap_version_probe(process)
        return CodexInstallation(path, None, False, False, "version_unavailable")
    except asyncio.CancelledError:
        await _reap_version_probe(process)
        raise

    if process.returncode != 0:
        return CodexInstallation(path, None, False, False, "version_unavailable")
    match = _CODEX_VERSION.fullmatch(stdout.decode("utf-8", errors="replace").strip())
    version = match.group(1) if match is not None else None
    parsed_version = _parse_release_version(version)
    if parsed_version is None or parsed_version < _MINIMUM_CODEX_VERSION:
        return CodexInstallation(path, version, False, False, "unsupported_version")
    return CodexInstallation(path, version, True, version in _SCHEMA_VERIFIED_CODEX_VERSIONS, None)


class CodexTurn:
    """One ephemeral Codex turn dispatched through a shared app-server process."""

    def __init__(
        self,
        peer: CodexAppServer,
        thread_id: str,
        *,
        requested_model: str,
        effective_model: str,
        effective_model_provider: str,
    ) -> None:
        self._peer: CodexAppServer = peer
        self.thread_id: str = thread_id
        self.turn_id: str | None = None
        self.requested_model: str = requested_model
        self.effective_model: str = effective_model
        self.effective_model_provider: str = effective_model_provider
        self.reasoning_effort: Literal["low"] = _REPLY_REASONING_EFFORT
        self._queue: asyncio.Queue[str | CodexSafeError | object] = asyncio.Queue(maxsize=_DELTA_QUEUE_LIMIT)
        self._finished: bool = False
        self._subscription_cleanup_claimed: bool = False

    @property
    def finished(self) -> bool:
        return self._finished

    def claim_subscription_cleanup(self) -> bool:
        if self._subscription_cleanup_claimed:
            return False
        self._subscription_cleanup_claimed = True
        return True

    def emit(self, delta: str) -> None:
        if self._finished or not delta:
            return
        if len(delta) > _DELTA_SIZE_LIMIT or self._queue.full():
            self._finished = True
            self._replace_queue_if_full(
                CodexSafeError(
                    "stream_backpressure",
                    "Codex の応答を安全に受信できませんでした。もう一度お試しください。",
                    retryable=True,
                )
            )
            self._peer.spawn_background(
                self._peer.interrupt_overflow_turn(self),
                "codex-backpressure-interrupt",
            )
            return
        self._queue.put_nowait(delta)

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._replace_queue_if_full(_END)
        self._peer.turn_finished(self)

    def fail(self, error: CodexSafeError) -> None:
        if self._finished:
            return
        self._finished = True
        self._replace_queue_if_full(error)
        self._peer.turn_finished(self)

    def _replace_queue_if_full(self, item: object) -> None:
        if self._queue.full():
            while not self._queue.empty():
                _ = self._queue.get_nowait()
        self._queue.put_nowait(item)

    async def interrupt(self) -> None:
        await self._peer.interrupt_turn(self)

    async def deltas(self) -> AsyncIterator[str]:
        try:
            while True:
                item = await self._queue.get()
                if item is _END:
                    return
                if isinstance(item, CodexSafeError):
                    raise item
                if isinstance(item, str):
                    yield item
        finally:
            if not self.finished:
                await self.interrupt()


class CodexAppServer:
    """Long-lived Codex app-server process with typed request and turn routing."""

    _configured_binary: str | os.PathLike[str] | None
    _configured_work_root: str | os.PathLike[str] | None
    _installation: CodexInstallation
    process_state: ProcessState
    auth_state: AuthState
    turn_state: TurnState
    _stderr_size: int
    _request_id: int
    _write_lock: asyncio.Lock
    _start_lock: asyncio.Lock
    _turn_lock: asyncio.Lock
    _model_catalog: dict[str, ModelListItem] | None
    last_model_selection: CodexModelSelection | None

    def __init__(
        self,
        *,
        binary: str | os.PathLike[str] | None = None,
        work_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self._configured_binary = binary
        self._configured_work_root = work_root
        self._installation = CodexInstallation(None, None, False, False, "unchecked")
        self.process_state = ProcessState.STOPPED
        self.auth_state = AuthState.UNKNOWN
        self.turn_state = TurnState.IDLE
        self._account: CodexAccountSnapshot | None = None
        self._process: Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._crash_cleanup_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._stderr_chunks: deque[bytes] = deque()
        self._stderr_size = 0
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self._cwd: Path | None = None
        self._isolated_instructions: Path | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, object]]] = {}
        self._write_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._active_turn: CodexTurn | None = None
        self._login_id: str | None = None

        self._model_catalog = None
        self.last_model_selection = None

    async def __aenter__(self) -> Self:
        await self.ensure_ready()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        _ = exc_info
        await self.close()

    @property
    def cwd(self) -> Path:
        if self._cwd is None:
            raise CodexSafeError("runtime_not_ready", "Codex はまだ起動していません。", retryable=True)
        return self._cwd

    @property
    def isolated_instructions(self) -> Path:
        if self._isolated_instructions is None:
            raise CodexSafeError("runtime_not_ready", "Codex はまだ起動していません。", retryable=True)
        return self._isolated_instructions

    async def status(self, *, refresh_account: bool = True) -> CodexStatusSnapshot:
        self._installation = await inspect_codex_installation(self._configured_binary)
        if self._installation.compatible and refresh_account:
            try:
                await self.ensure_ready()
                _ = await self.read_account()
            except CodexSafeError:
                if self.process_state is not ProcessState.CLOSED:
                    if self._process is None:
                        self.process_state = ProcessState.FAILED
                    else:
                        self._fail_process("status_probe_failed")
                        self._schedule_crash_cleanup()
        return CodexStatusSnapshot(
            installation=self._installation,
            process_state=self.process_state,
            auth_state=self.auth_state,
            turn_state=self.turn_state,
            account=self._account,
        )

    async def ensure_ready(self) -> None:
        async with self._start_lock:
            if self.process_state is ProcessState.READY:
                return
            if self.process_state is ProcessState.CLOSED:
                raise CodexSafeError("runtime_closed", "Codex runtime は終了しています。", retryable=False)
            if self.process_state is ProcessState.FAILED:
                await self._stop_process(final_state=ProcessState.STOPPED)

            self._installation = await inspect_codex_installation(self._configured_binary)
            if not self._installation.compatible or self._installation.binary is None:
                code = self._installation.reason_code or "not_available"
                message = {
                    "unsupported_version": f"Codex CLI {MINIMUM_CODEX_VERSION_LABEL} が必要です。",
                    "version_unavailable": "Codex CLI のバージョンを確認できません。",
                }.get(code, "Codex CLI を利用できません。")
                raise CodexSafeError(code, message, retryable=code == "version_unavailable")

            work_root = self._resolve_work_root()
            try:
                self._temporary_directory = tempfile.TemporaryDirectory(
                    prefix="meeting-supporter-codex-",
                    dir=os.fspath(work_root) if work_root is not None else None,
                )
            except OSError as exc:
                raise CodexSafeError(
                    "work_directory_unavailable",
                    "Codex の安全な作業領域を作成できません。",
                    retryable=False,
                ) from exc
            temporary_root = Path(self._temporary_directory.name).resolve()
            self._cwd = temporary_root / "cwd"
            self._isolated_instructions = temporary_root / "instructions.txt"
            try:
                self._cwd.mkdir(mode=0o700)
                _ = self._isolated_instructions.write_text(_REPLY_INSTRUCTIONS, encoding="utf-8")
                self._isolated_instructions.chmod(0o600)
            except OSError as exc:
                await self._cleanup_temporary_directory()
                raise CodexSafeError(
                    "work_directory_unavailable",
                    "Codex の安全な作業領域を初期化できません。",
                    retryable=False,
                ) from exc
            self.process_state = ProcessState.STARTING
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *_codex_process_command(
                        self._installation.binary,
                        "-c",
                        "mcp_servers={}",
                        "-c",
                        'web_search="disabled"',
                        "-c",
                        "features.shell_tool=false",
                        "-c",
                        "features.shell_snapshot=false",
                        "-c",
                        "features.multi_agent=false",
                        "-c",
                        "features.skill_mcp_dependency_install=false",
                        "-c",
                        "tools.web_search=false",
                        "-c",
                        "tools.view_image=false",
                        "-c",
                        "apps={}",
                        "-c",
                        "plugins={}",
                        "-c",
                        "skills.config=[]",
                        "-c",
                        "project_doc_max_bytes=0",
                        "-c",
                        "allow_login_shell=false",
                        "-c",
                        "hooks={}",
                        "-c",
                        "notify=[]",
                        "-c",
                        'shell_environment_policy.inherit="none"',
                        "-c",
                        f"model_instructions_file={json.dumps(os.fspath(self.isolated_instructions))}",
                        "app-server",
                        "--stdio",
                    ),
                    cwd=self._cwd,
                    env=_child_environment(os.environ),
                    stdin=PIPE,
                    stdout=PIPE,
                    stderr=PIPE,
                    limit=_STDIO_LIMIT,
                )
            except OSError as exc:
                self.process_state = ProcessState.FAILED
                await self._cleanup_temporary_directory()
                raise CodexSafeError(
                    "process_start_failed",
                    "Codex app-server を起動できませんでした。",
                    retryable=True,
                ) from exc

            self._reader_task = asyncio.create_task(self._reader_loop(), name="codex-app-server-reader")
            self._stderr_task = asyncio.create_task(self._stderr_loop(), name="codex-app-server-stderr")
            try:
                params = InitializeParams(
                    clientInfo=ClientInfo(name="meeting-supporter", title="Meeting Supporter", version="0.1.0"),
                    capabilities=InitializeCapabilities(
                        experimentalApi=False,
                        requestAttestation=False,
                        mcpServerOpenaiFormElicitation=False,
                    ),
                )
                raw = await self._request_started("initialize", params, timeout=_INITIALIZE_TIMEOUT)
                _ = InitializeResult.model_validate(raw)
                await self._notify("initialized")
            except (CodexSafeError, ValidationError) as exc:
                self.process_state = ProcessState.FAILED
                await self._stop_process(final_state=ProcessState.FAILED)
                if isinstance(exc, CodexSafeError):
                    raise
                raise CodexSafeError(
                    "protocol_incompatible",
                    "Codex app-server の初期化応答に互換性がありません。",
                    retryable=False,
                ) from exc
            self.process_state = ProcessState.READY

    def _resolve_work_root(self) -> Path | None:
        raw = self._configured_work_root
        if raw is None:
            raw = os.environ.get("MEETING_SUPPORTER_CODEX_WORK_ROOT")
        if raw is None:
            return None
        root = Path(raw)
        if not root.is_absolute() or not root.is_dir():
            raise CodexSafeError(
                "invalid_work_root",
                "Codex の作業領域設定が無効です。",
                retryable=False,
            )
        return root.resolve()

    async def read_account(self) -> CodexAccountSnapshot:
        raw = await self.request("account/read", AccountReadParams(refreshToken=False))
        try:
            response = AccountReadResult.model_validate(raw)
        except ValidationError as exc:
            raise self._protocol_error() from exc
        account = response.account
        authenticated = account is not None and account.type == "chatgpt"
        self.auth_state = AuthState.AUTHENTICATED if authenticated else AuthState.UNAUTHENTICATED
        self._account = CodexAccountSnapshot(
            authenticated=authenticated,
            account_type=account.type if account is not None else None,
            plan_type=account.plan_type if account is not None else None,
            requires_openai_auth=response.requires_openai_auth,
        )
        if not authenticated:
            self._model_catalog = None
            self.last_model_selection = None
        return self._account

    async def is_model_available(self, model: str, *, effort: Literal["low"] | None = None) -> bool:
        """Return whether an authenticated app-server exposes the requested visible model and effort."""
        if not model:
            return False
        await self.ensure_ready()
        account = await self.read_account()
        if not account.authenticated:
            code = "unsupported_auth" if account.account_type is not None else "not_logged_in"
            raise CodexSafeError(code, "ChatGPT で Codex にログインしてください。", retryable=False)
        candidate = (await self._read_visible_models()).get(model)
        return candidate is not None and (
            effort is None or any(option.reasoning_effort == effort for option in candidate.supported_reasoning_efforts)
        )

    async def model_service_tier(
        self, model: str, *, effort: Literal["low"] | None = None
    ) -> Literal["standard", "priority"]:
        """Return the requested model's advertised tier without exposing catalog internals."""
        if not model:
            raise CodexSafeError("model_not_configured", "Codex経路のモデル設定が空です。", retryable=False)
        await self.ensure_ready()
        account = await self.read_account()
        if not account.authenticated:
            code = "unsupported_auth" if account.account_type is not None else "not_logged_in"
            raise CodexSafeError(code, "ChatGPT で Codex にログインしてください。", retryable=False)
        candidate = (await self._read_visible_models()).get(model)
        if candidate is None or (
            effort is not None
            and not any(option.reasoning_effort == effort for option in candidate.supported_reasoning_efforts)
        ):
            raise CodexSafeError("model_unavailable", "指定されたCodexモデルを利用できません。", retryable=False)
        return (
            _PRIORITY_SERVICE_TIER
            if any(tier.id == _PRIORITY_SERVICE_TIER for tier in candidate.service_tiers)
            else _STANDARD_SERVICE_TIER
        )

    async def model_service_tier_status(
        self, model: str, *, effort: Literal["low"] | None = None
    ) -> CodexServiceTierStatus:
        """Return current catalog tier plus the latest same-model observed effective tier."""
        advertised = await self.model_service_tier(model, effort=effort)
        selection = self.last_model_selection
        effective: Literal["standard", "priority"] | None = None
        if selection is not None and selection.requested_model == model:
            effective = (
                _PRIORITY_SERVICE_TIER
                if selection.effective_service_tier == _PRIORITY_SERVICE_TIER
                else _STANDARD_SERVICE_TIER
            )
        return CodexServiceTierStatus(advertised=advertised, effective=effective)

    async def _read_visible_models(self) -> dict[str, ModelListItem]:
        catalog = self._model_catalog
        if catalog is not None:
            return catalog

        visible: dict[str, ModelListItem] = {}
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(_MODEL_LIST_MAX_PAGES):
            raw = await self.request(
                "model/list",
                ModelListParams(cursor=cursor, limit=_MODEL_LIST_PAGE_SIZE, includeHidden=False),
            )
            try:
                result = ModelListResult.model_validate(raw)
            except ValidationError as exc:
                raise self._protocol_error() from exc
            for item in result.data:
                if not item.hidden and item.model:
                    visible[item.model] = item
            cursor = result.next_cursor
            if cursor is None:
                self._model_catalog = visible
                return visible
            if cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
        raise self._protocol_error()

    async def start_login(self) -> ChatGptLoginStartResult:
        raw = await self.request("account/login/start", ChatGptLoginStartParams())
        try:
            result = ChatGptLoginStartResult.model_validate(raw)
        except ValidationError as exc:
            raise self._protocol_error() from exc
        self._login_id = result.login_id
        self.auth_state = AuthState.LOGGING_IN
        return result

    async def start_device_code_login(self) -> DeviceCodeLoginStartResult:
        raw = await self.request("account/login/start", DeviceCodeLoginStartParams())
        try:
            result = DeviceCodeLoginStartResult.model_validate(raw)
        except ValidationError as exc:
            raise self._protocol_error() from exc
        self._login_id = result.login_id
        self.auth_state = AuthState.LOGGING_IN
        return result

    async def cancel_login(self, login_id: str | None = None) -> LoginCancelResult:
        selected_login_id = login_id or self._login_id
        if selected_login_id is None:
            return LoginCancelResult(status="notFound")
        raw = await self.request("account/login/cancel", LoginCancelParams(loginId=selected_login_id))
        try:
            result = LoginCancelResult.model_validate(raw)
        except ValidationError as exc:
            raise self._protocol_error() from exc
        if selected_login_id == self._login_id:
            self._login_id = None
            self.auth_state = AuthState.UNKNOWN
        return result

    async def logout(self) -> None:
        raw = await self.request("account/logout", EmptyParams())
        try:
            _ = EmptyResult.model_validate(raw)
        except ValidationError as exc:
            raise self._protocol_error() from exc
        self._login_id = None
        self._account = None
        self._model_catalog = None
        self.last_model_selection = None
        self.auth_state = AuthState.UNAUTHENTICATED

    async def read_rate_limits(self) -> RateLimitsReadResult:
        raw = await self.request("account/rateLimits/read", EmptyParams())
        try:
            return RateLimitsReadResult.model_validate(raw)
        except ValidationError as exc:
            raise self._protocol_error() from exc

    async def begin_turn(self, prompt: str, model: str, *, instructions: str) -> CodexTurn:
        if not prompt:
            raise CodexSafeError("empty_prompt", "応答の入力が空です。", retryable=False)
        if not model:
            raise CodexSafeError("model_not_configured", "Codex経路のモデル設定が空です。", retryable=False)
        await self.ensure_ready()
        account = await self.read_account()
        if not account.authenticated:
            code = "unsupported_auth" if account.account_type is not None else "not_logged_in"
            raise CodexSafeError(code, "ChatGPT で Codex にログインしてください。", retryable=False)
        candidate = (await self._read_visible_models()).get(model)
        if candidate is None:
            raise CodexSafeError("model_unavailable", "指定されたCodexモデルを利用できません。", retryable=False)
        if not any(
            option.reasoning_effort == _REPLY_REASONING_EFFORT for option in candidate.supported_reasoning_efforts
        ):
            raise CodexSafeError(
                "reasoning_effort_unavailable",
                "指定されたCodexモデルでは低い推論強度を利用できません。",
                retryable=False,
            )
        service_tier = (
            _PRIORITY_SERVICE_TIER
            if any(tier.id == _PRIORITY_SERVICE_TIER for tier in candidate.service_tiers)
            else _STANDARD_SERVICE_TIER
        )
        _ = await self._turn_lock.acquire()
        session: CodexTurn | None = None
        try:
            thread_raw = await self.request(
                "thread/start",
                ThreadStartParams(
                    cwd=os.fspath(self.cwd),
                    approvalPolicy="never",
                    sandbox="read-only",
                    ephemeral=True,
                    config={
                        "allow_login_shell": False,
                        "apps": {},
                        "hooks": {},
                        "model_instructions_file": os.fspath(self.isolated_instructions),
                        "notify": [],
                        "features": {
                            "multi_agent": False,
                            "shell_snapshot": False,
                            "shell_tool": False,
                            "skill_mcp_dependency_install": False,
                        },
                        "mcp_servers": {},
                        "plugins": {},
                        "project_doc_max_bytes": 0,
                        "skills": {"config": []},
                        "tools": {"view_image": False, "web_search": False},
                        "shell_environment_policy": {"inherit": "none"},
                        "web_search": "disabled",
                    },
                    baseInstructions=instructions,
                    developerInstructions=instructions,
                    model=model,
                    serviceTier=service_tier if service_tier == _PRIORITY_SERVICE_TIER else None,
                ),
                timeout=_TURN_START_TIMEOUT,
            )
            thread = ThreadStartResult.model_validate(thread_raw)
            if Path(thread.cwd).resolve() != self.cwd:
                raise self._protocol_error()
            session = CodexTurn(
                self,
                thread.thread.id,
                requested_model=model,
                effective_model=thread.model,
                effective_model_provider=thread.model_provider,
            )
            if thread.model != model:
                logger.warning(
                    "Codex model rerouted before turn requested_model=%s "
                    + "effective_model=%s effective_model_provider=%s",
                    model,
                    thread.model,
                    thread.model_provider,
                )
                raise CodexSafeError(
                    "model_rerouted",
                    "Codexが指定されたモデル以外へ切り替えたため、応答を開始しませんでした。",
                    retryable=False,
                )
            self._active_turn = session
            self.turn_state = TurnState.STARTING
            turn_raw = await self.request(
                "turn/start",
                TurnStartParams(
                    threadId=thread.thread.id,
                    input=[TextUserInput(text=prompt)],
                    approvalPolicy="never",
                    cwd=os.fspath(self.cwd),
                    model=model,
                    effort=_REPLY_REASONING_EFFORT,
                    serviceTier=service_tier if service_tier == _PRIORITY_SERVICE_TIER else None,
                ),
                timeout=_TURN_START_TIMEOUT,
            )
            turn = TurnStartResult.model_validate(turn_raw)
            session.turn_id = turn.turn.id
            selection = CodexModelSelection(
                requested_model=model,
                effective_model=thread.model,
                effective_model_provider=thread.model_provider,
                reasoning_effort=_REPLY_REASONING_EFFORT,
                service_tier=service_tier,
                requested_service_tier=service_tier if service_tier == _PRIORITY_SERVICE_TIER else None,
                effective_service_tier=turn.turn.service_tier,
            )
            self.last_model_selection = selection
            if (
                selection.requested_service_tier == _PRIORITY_SERVICE_TIER
                and selection.effective_service_tier != _PRIORITY_SERVICE_TIER
            ):
                logger.warning(
                    "Codex service tier fallback requested_service_tier=%s effective_service_tier=%s",
                    selection.requested_service_tier,
                    selection.effective_service_tier,
                )
            logger.info(
                "Codex model selected requested_model=%s effective_model=%s "
                + "effective_model_provider=%s effort=%s advertised_tier=%s "
                + "requested_tier=%s effective_tier=%s",
                selection.requested_model,
                selection.effective_model,
                selection.effective_model_provider,
                selection.reasoning_effort,
                selection.service_tier,
                selection.requested_service_tier,
                selection.effective_service_tier,
            )
            if not session.finished:
                self.turn_state = TurnState.ACTIVE
            return session
        except ValidationError as exc:
            error = self._protocol_error()
            if session is not None:
                session.fail(error)
                await self._unsubscribe_turn(session)
            elif self._turn_lock.locked():
                self._turn_lock.release()
            await self._terminate_uncertain_process()
            raise error from exc
        except BaseException:
            if session is not None:
                session.fail(
                    CodexSafeError(
                        "turn_start_failed",
                        "Codex の応答を開始できませんでした。",
                        retryable=True,
                    )
                )
                await self._unsubscribe_turn(session)
                await self._terminate_uncertain_process()
            elif self._turn_lock.locked():
                self._turn_lock.release()
            raise

    async def begin_reply(self, prompt: str, model: str) -> CodexTurn:
        """Start the existing reply turn with its reply-specific instructions."""
        return await self.begin_turn(prompt, model, instructions=_REPLY_INSTRUCTIONS)

    async def interrupt_active_turn(self) -> bool:
        turn = self._active_turn
        if turn is None or turn.finished:
            return False
        await self.interrupt_turn(turn)
        return True

    async def interrupt_turn(self, turn: CodexTurn) -> None:
        if turn.finished:
            return
        if turn is not self._active_turn:
            turn.finish()
            return
        if turn.turn_id is None:
            turn.finish()
            await self._unsubscribe_turn(turn)
            await self._terminate_uncertain_process()
            return
        self.turn_state = TurnState.INTERRUPTING
        interrupt_failed = False
        try:
            raw = await self.request(
                "turn/interrupt",
                TurnInterruptParams(threadId=turn.thread_id, turnId=turn.turn_id),
                timeout=_INTERRUPT_TIMEOUT,
            )
            _ = TurnInterruptResult.model_validate(raw)
        except (CodexSafeError, ValidationError):
            interrupt_failed = True
        finally:
            turn.finish()
        if interrupt_failed:
            await self._unsubscribe_turn(turn)
            await self._terminate_uncertain_process()

    async def interrupt_overflow_turn(self, turn: CodexTurn) -> None:
        try:
            if turn.turn_id is not None:
                raw = await self.request(
                    "turn/interrupt",
                    TurnInterruptParams(threadId=turn.thread_id, turnId=turn.turn_id),
                    timeout=_INTERRUPT_TIMEOUT,
                )
                _ = TurnInterruptResult.model_validate(raw)
        except (CodexSafeError, ValidationError):
            await self._terminate_uncertain_process()
        finally:
            self.turn_finished(turn)

    async def _unsubscribe_turn(self, turn: CodexTurn) -> None:
        """Release one app-server notification subscription without reviving a failed peer."""
        if not turn.claim_subscription_cleanup():
            return
        process = self._process
        if (
            self.process_state is not ProcessState.READY
            or process is None
            or process.stdin is None
            or process.returncode is not None
        ):
            logger.info("Skipping Codex thread subscription cleanup because the process is unavailable")
            return
        try:
            raw = await self._request_started(
                "thread/unsubscribe",
                ThreadUnsubscribeParams(threadId=turn.thread_id),
                timeout=_INTERRUPT_TIMEOUT,
            )
            _ = ThreadUnsubscribeResult.model_validate(raw)
        except (CodexSafeError, ValidationError):
            logger.warning("Codex thread subscription cleanup failed")

    async def request(
        self,
        method: str,
        params: CodexProtocolModel | dict[str, object],
        *,
        timeout: float = _REQUEST_TIMEOUT,
    ) -> dict[str, object]:
        await self.ensure_ready()
        return await self._request_started(method, params, timeout=timeout)

    async def _request_started(
        self,
        method: str,
        params: CodexProtocolModel | dict[str, object],
        *,
        timeout: float,
    ) -> dict[str, object]:
        self._request_id += 1
        request_id = self._request_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, object]] = loop.create_future()
        self._pending[request_id] = future
        payload_params = (
            params.model_dump(by_alias=True, exclude_none=True) if isinstance(params, CodexProtocolModel) else params
        )
        try:
            await self._send({"id": request_id, "method": method, "params": payload_params})
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            raise CodexSafeError(
                "request_timeout",
                "Codex から時間内に応答がありませんでした。",
                retryable=True,
            ) from exc
        finally:
            if not future.done():
                _ = future.cancel()
            _ = self._pending.pop(request_id, None)

    async def _notify(self, method: str) -> None:
        await self._send({"method": method})

    async def _send(self, payload: dict[str, object]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise CodexSafeError(
                "process_unavailable",
                "Codex app-server が停止しています。",
                retryable=True,
            )
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        async with self._write_lock:
            try:
                process.stdin.write(data)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionError) as exc:
                self._fail_process("process_exited")
                raise CodexSafeError(
                    "process_exited",
                    "Codex app-server が予期せず終了しました。",
                    retryable=True,
                ) from exc

    async def _reader_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._fail_process("process_unavailable")
            return
        try:
            while line := await process.stdout.readline():
                if len(line) > _STDIO_LIMIT:
                    raise ValueError("oversized protocol line")
                message = _JSON_OBJECT.validate_json(line)
                await self._handle_message(message)
        except (UnicodeError, ValidationError, ValueError):
            self._fail_process("protocol_error")
            if process.returncode is None:
                _ = process.terminate()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._fail_process("process_read_failed")
        else:
            if self.process_state not in {ProcessState.CLOSED, ProcessState.STOPPED}:
                self._fail_process("process_exited")
        if self.process_state is ProcessState.FAILED:
            self._schedule_crash_cleanup()

    def _schedule_crash_cleanup(self) -> None:
        if self._crash_cleanup_task is not None and not self._crash_cleanup_task.done():
            return
        self._crash_cleanup_task = asyncio.create_task(self._cleanup_after_crash(), name="codex-crash-cleanup")

    async def _cleanup_after_crash(self) -> None:
        async with self._start_lock:
            if self.process_state is ProcessState.FAILED:
                await self._stop_process(final_state=ProcessState.FAILED)

    def _notification_targets_active_turn(self, method: str, params: dict[str, object]) -> bool:
        turn = self._active_turn
        if method not in _TURN_NOTIFICATION_METHODS or turn is None or turn.finished:
            return False
        thread_id = params.get("threadId")
        if isinstance(thread_id, str) and thread_id != turn.thread_id:
            return False
        turn_id: object = params.get("turnId")
        if method == "turn/completed":
            raw_turn = params.get("turn")
            turn_id = _JSON_OBJECT.validate_python(raw_turn).get("id") if isinstance(raw_turn, dict) else None
        return not isinstance(turn_id, str) or turn.turn_id is None or turn_id == turn.turn_id

    async def _terminate_malformed_turn_notification(self, turn: CodexTurn, error: CodexSafeError) -> None:
        try:
            await self._unsubscribe_turn(turn)
            await self._terminate_uncertain_process(turn_error=error)
        finally:
            turn.fail(error)
            if self._turn_lock.locked():
                self._turn_lock.release()

    def _fail_active_turn_protocol_notification(self, method: str, params: dict[str, object]) -> None:
        if not self._notification_targets_active_turn(method, params):
            return
        turn = self._active_turn
        if turn is None:
            return
        self._active_turn = None
        self.spawn_background(
            self._terminate_malformed_turn_notification(turn, self._protocol_error()),
            "codex-malformed-turn-notification-cleanup",
        )

    async def _handle_message(self, message: dict[str, object]) -> None:
        request_id = message.get("id")
        method = message.get("method")
        if method is None and isinstance(request_id, int):
            future = self._pending.get(request_id)
            if future is None or future.done():
                return
            result = message.get("result")
            if isinstance(result, dict):
                try:
                    future.set_result(_JSON_OBJECT.validate_python(result))
                    # Yield so turn/start can register its turn id before buffered notifications route.
                    await asyncio.sleep(0)
                except ValidationError:
                    future.set_exception(self._protocol_error())
            elif "error" in message:
                future.set_exception(
                    CodexSafeError(
                        "request_rejected",
                        "Codex が要求を完了できませんでした。",
                        retryable=False,
                    )
                )
            else:
                future.set_exception(self._protocol_error())
            return

        if not isinstance(method, str):
            return
        if request_id is not None:
            await self._deny_server_request(request_id, method)
            return
        params = message.get("params")
        if not isinstance(params, dict):
            self._fail_active_turn_protocol_notification(method, {})
            return
        try:
            normalized = _JSON_OBJECT.validate_python(params)
        except ValidationError:
            self._fail_active_turn_protocol_notification(method, {})
            return
        try:
            if method == "item/agentMessage/delta":
                event = AgentMessageDeltaNotification.model_validate(normalized)
                turn = self._active_turn
                if turn is not None and event.thread_id == turn.thread_id:
                    if turn.turn_id is None or event.turn_id == turn.turn_id:
                        turn.emit(event.delta)
            elif method == "turn/completed":
                event = TurnCompletedNotification.model_validate(normalized)
                turn = self._active_turn
                if turn is not None and event.thread_id == turn.thread_id:
                    if turn.turn_id is None:
                        turn.turn_id = event.turn.id
                    if event.turn.id == turn.turn_id:
                        if event.turn.status == "failed":
                            turn.fail(
                                self._safe_turn_error(event.turn.error.codex_error_info if event.turn.error else None)
                            )
                        else:
                            turn.finish()
            elif method == "model/rerouted":
                event = ModelReroutedNotification.model_validate(normalized)
                turn = self._active_turn
                if turn is not None and event.thread_id == turn.thread_id:
                    if turn.turn_id is None:
                        turn.turn_id = event.turn_id
                    if event.turn_id == turn.turn_id:
                        turn.fail(
                            CodexSafeError(
                                "model_rerouted",
                                "Codexが指定されたモデル以外へ切り替えたため、応答を中止しました。",
                                retryable=False,
                            )
                        )
                        self.spawn_background(
                            self.interrupt_overflow_turn(turn),
                            "codex-model-reroute-interrupt",
                        )
            elif method == "account/login/completed":
                event = AccountLoginCompletedNotification.model_validate(normalized)
                if event.login_id is None or event.login_id == self._login_id:
                    self._login_id = None
                    self.auth_state = AuthState.UNKNOWN if event.success else AuthState.UNAUTHENTICATED
                    self._model_catalog = None
        except ValidationError:
            self._fail_active_turn_protocol_notification(method, normalized)

    async def _deny_server_request(self, request_id: object, method: str) -> None:
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            await self._send({"id": request_id, "result": {"decision": "cancel"}})
        else:
            await self._send(
                {
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": "Client capability is disabled",
                    },
                }
            )
        turn = self._active_turn
        if turn is not None and not turn.finished:
            self.spawn_background(self.interrupt_turn(turn), "codex-denied-capability-interrupt")

    def _safe_turn_error(self, info: str | dict[str, object] | None) -> CodexSafeError:
        code = info if isinstance(info, str) else None
        if code == "unauthorized":
            self.auth_state = AuthState.UNAUTHENTICATED
            return CodexSafeError("not_logged_in", "Codex へ再度ログインしてください。", retryable=False)
        if code in {"usageLimitExceeded", "sessionBudgetExceeded"}:
            return CodexSafeError("usage_limit", "Codex の利用上限に達しました。", retryable=False)
        if code in {"serverOverloaded", "internalServerError"}:
            return CodexSafeError("service_unavailable", "Codex を一時的に利用できません。", retryable=True)
        return CodexSafeError("turn_failed", "Codex が応答を完了できませんでした。", retryable=True)

    def turn_finished(self, turn: CodexTurn) -> None:
        if self._active_turn is turn:
            self._active_turn = None
            self.turn_state = TurnState.IDLE
            if self._turn_lock.locked():
                self._turn_lock.release()
        self.spawn_background(self._unsubscribe_turn(turn), "codex-thread-unsubscribe")

    def _fail_process(self, code: str) -> None:
        if self.process_state in {ProcessState.CLOSED, ProcessState.STOPPED}:
            return
        self.process_state = ProcessState.FAILED
        error = CodexSafeError(code, "Codex app-server との接続が終了しました。", retryable=True)
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        if self._active_turn is not None:
            self._active_turn.fail(error)

    async def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while chunk := await process.stderr.read(4096):
                self._stderr_chunks.append(chunk)
                self._stderr_size += len(chunk)
                while self._stderr_size > _STDERR_LIMIT and self._stderr_chunks:
                    removed = self._stderr_chunks.popleft()
                    self._stderr_size -= len(removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    def spawn_background(self, coroutine: Coroutine[object, object, None], name: str) -> None:
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _terminate_uncertain_process(self, *, turn_error: CodexSafeError | None = None) -> None:
        self.process_state = ProcessState.FAILED
        async with self._start_lock:
            await self._stop_process(final_state=ProcessState.FAILED, turn_error=turn_error)

    async def close(self) -> None:
        async with self._start_lock:
            await self._stop_process(final_state=ProcessState.CLOSED)

    async def _stop_process(
        self,
        *,
        final_state: ProcessState,
        turn_error: CodexSafeError | None = None,
    ) -> None:
        process = self._process
        if process is not None and process.stdin is not None:
            process.stdin.close()
        if process is not None and process.returncode is None:
            try:
                _ = await asyncio.wait_for(process.wait(), timeout=0.2)
            except TimeoutError:
                process.terminate()
                try:
                    _ = await asyncio.wait_for(process.wait(), timeout=2.0)
                except TimeoutError:
                    process.kill()
                    _ = await process.wait()
        current = asyncio.current_task()
        managed_tasks = (self._reader_task, self._stderr_task, *tuple(self._background_tasks))
        for task in managed_tasks:
            if task is not None and task is not current and not task.done():
                _ = task.cancel()
        for task in managed_tasks:
            if task is not None and task is not current:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._reader_task = None
        self._stderr_task = None
        self._background_tasks.clear()
        self._process = None
        self._stderr_chunks.clear()
        self._stderr_size = 0
        error = turn_error or CodexSafeError("runtime_closed", "Codex runtime は終了しました。", retryable=False)
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        if self._active_turn is not None:
            self._active_turn.fail(error)
        self._active_turn = None
        self.turn_state = TurnState.IDLE
        await self._cleanup_temporary_directory()
        self._model_catalog = None
        self.last_model_selection = None
        self.process_state = final_state

    async def _cleanup_temporary_directory(self) -> None:
        temporary_directory = self._temporary_directory
        self._temporary_directory = None
        self._cwd = None
        self._isolated_instructions = None
        if temporary_directory is None:
            return

        for retry_delay in (0.05, 0.1, 0.2, 0.4, None):
            try:
                temporary_directory.cleanup()
                return
            except PermissionError:
                if os.name != "nt" or retry_delay is None:
                    raise
                await asyncio.sleep(retry_delay)

    @staticmethod
    def _protocol_error() -> CodexSafeError:
        return CodexSafeError(
            "protocol_incompatible",
            "Codex app-server の応答に互換性がありません。",
            retryable=False,
        )


__all__ = [
    "AuthState",
    "CodexAccountSnapshot",
    "CodexModelSelection",
    "CodexAppServer",
    "CodexInstallation",
    "CodexSafeError",
    "CodexStatusSnapshot",
    "CodexTurn",
    "ProcessState",
    "TurnState",
    "MINIMUM_CODEX_VERSION_LABEL",
    "CodexServiceTierStatus",
    "inspect_codex_installation",
]
