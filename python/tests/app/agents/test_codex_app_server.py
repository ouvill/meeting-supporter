# pyright: reportAny=false, reportExplicitAny=false, reportPrivateUsage=false, reportUnannotatedClassAttribute=false
"""Contract and security tests for the isolated Codex app-server boundary."""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any, override
from unittest.mock import AsyncMock, Mock, patch

from app.agents.codex_app_server import (
    AuthState,
    CodexAppServer,
    CodexSafeError,
    ProcessState,
    TurnState,
    _reap_version_probe,
    inspect_codex_installation,
)
from app.agents.codex_runtime import CodexInfoAgentRuntime, CodexMinutesAgentRuntime, CodexReplyAgentRuntime
from app.agents.models import InfoPrompt, MinutesPrompt, ReplyPrompt
from app.agents.prompts import CODEX_INFO_INSTRUCTION, MINUTES_INSTRUCTION
from app.api.ai_runtimes import probe_codex_route_status

_CANARY = "codex-global-config-canary-8d8b13"
_LUNA = "gpt-5.6-luna"
_FAKE_CODEX_SEQUENCE = itertools.count()


def _write_fake_codex(directory: Path, *, mode: str, version: str = "0.144.0") -> tuple[Path, Path]:
    """Create a deterministic JSONL peer and a transcript it alone can access."""
    instance = next(_FAKE_CODEX_SEQUENCE)
    transcript = directory / f"transcript-{instance}.jsonl"
    script = directory / (f"fake-codex-{instance}.py" if os.name == "nt" else f"fake-codex-{instance}")
    _ = script.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json
            import os
            import sys
            from pathlib import Path

            if os.name == "nt":
                sys.stdin.reconfigure(encoding="utf-8")
                sys.stdout.reconfigure(encoding="utf-8")
                sys.stderr.reconfigure(encoding="utf-8")

            TRANSCRIPT = Path({str(transcript)!r})
            MODE = {mode!r}
            VERSION = {version!r}
            CANARY = {_CANARY!r}

            def record(kind, value):
                with TRANSCRIPT.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({{"kind": kind, "value": value}}, ensure_ascii=False) + "\\n")
                    stream.flush()

            def send(payload):
                sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\\n")
                sys.stdout.flush()
                record("sent", payload)

            def unsafe_global_config_visible():
                paths = []
                codex_home = os.environ.get("CODEX_HOME")
                home = os.environ.get("HOME")
                if codex_home:
                    paths.append(Path(codex_home) / "config.toml")
                if home:
                    paths.append(Path(home) / ".codex" / "config.toml")
                return any(path.is_file() and CANARY in path.read_text(encoding="utf-8") for path in paths)

            if "--version" in sys.argv:
                print("codex-cli " + VERSION)
                raise SystemExit(0)

            record("startup", {{
                "argv": sys.argv[1:],
                "codexHome": os.environ.get("CODEX_HOME"),
                "home": os.environ.get("HOME"),
                "globalConfigVisible": unsafe_global_config_visible(),
                "secretCanary": os.environ.get("MEETING_SUPPORTER_SECRET_CANARY"),
                "pid": os.getpid(),
            }})
            if MODE == "stderr":
                sys.stderr.buffer.write((CANARY.encode("utf-8") + b"x" * 70000))
                sys.stderr.flush()

            thread_number = 0
            for raw in sys.stdin:
                message = json.loads(raw)
                record("received", message)
                method = message.get("method")
                request_id = message.get("id")
                if method == "initialize":
                    initialize_result = {{
                        "userAgent": "fake-codex",
                        "platformFamily": "unix",
                        "platformOs": "linux",
                        "codexHome": "/isolated",
                    }}
                    if MODE == "initialize-missing-field":
                        del initialize_result["codexHome"]
                    send({{"id": request_id, "result": initialize_result}})
                elif method == "initialized":
                    record("initialized", True)
                elif method == "account/read":
                    if MODE == "eof":
                        raise SystemExit(0)
                    if MODE == "malformed":
                        sys.stdout.write("{{not-json}}\\n")
                        sys.stdout.flush()
                        continue
                    if MODE == "timeout":
                        continue
                    account = None if MODE == "unauthenticated" else {{"type": "chatgpt", "planType": "plus"}}
                    account_result = {{"account": account, "requiresOpenaiAuth": account is None}}
                    if MODE == "account-missing-field":
                        del account_result["requiresOpenaiAuth"]
                    send({{"id": request_id, "result": account_result}})
                elif method == "account/login/start":
                    send({{"id": request_id, "result": {{"type": "chatgpt", "loginId": "login-1", "authUrl": "https://login.invalid/"}}}})
                elif method == "probe/login-completion":
                    if MODE == "auth":
                        send(
                            {{
                                "method": "account/login/completed",
                                "params": {{"loginId": "login-1", "success": False}},
                            }}
                        )
                    send({{"id": request_id, "result": {{}}}})
                elif method == "model/list":
                    if MODE == "model-unavailable":
                        models = []
                    else:
                        efforts = (
                            []
                            if MODE == "low-unavailable"
                            else [{{"reasoningEffort": "low", "description": "低遅延"}}]
                        )
                        service_tiers = (
                            [{{"id": "priority", "name": "Priority", "description": "速度優先"}}]
                            if MODE in {{"priority", "priority-standard"}}
                            else []
                        )
                        models = [{{
                            "id": "luna-1",
                            "model": "gpt-5.6-luna",
                            "displayName": "GPT-5.6 Luna",
                            "description": "低遅延応答",
                            "hidden": False,
                            "supportedReasoningEfforts": efforts,
                            "defaultReasoningEffort": "low",
                            "isDefault": True,
                            "serviceTiers": service_tiers,
                        }}]
                    model_list_result = {{"data": models, "nextCursor": None}}
                    if MODE == "model-missing-field":
                        del model_list_result["nextCursor"]
                    send({{"id": request_id, "result": model_list_result}})
                elif method == "thread/start":
                    thread_number += 1
                    thread_id = f"thread-{{thread_number}}" if MODE == "sequential" else "thread-1"
                    thread_result = {{
                        "thread": {{"id": thread_id}},
                        "approvalPolicy": "never",
                        "cwd": message["params"]["cwd"],
                        "model": "gpt-5.6-luna-rerouted" if MODE == "thread-rerouted" else "gpt-5.6-luna",
                        "modelProvider": "chatgpt",
                        "sandbox": {{"type": "readOnly", "networkAccess": False}},
                        "serviceTier": "priority" if MODE in {{"priority", "priority-standard"}} else None,
                    }}
                    if MODE == "thread-missing-field":
                        del thread_result["thread"]["id"]
                    send({{"id": request_id, "result": thread_result}})
                elif method == "turn/start":
                    effective_tier = (
                        "standard"
                        if MODE == "priority-standard"
                        else "priority" if MODE == "priority" else None
                    )
                    turn_result = {{
                        "turn": {{
                            "id": (
                                f"turn-{{message['params']['threadId'].rsplit('-', 1)[1]}}"
                                if MODE == "sequential"
                                else "turn-1"
                            ),
                            "status": "inProgress",
                            "serviceTier": effective_tier,
                        }}
                    }}
                    if MODE == "turn-missing-field":
                        del turn_result["turn"]["id"]
                    send({{"id": request_id, "result": turn_result}})
                    if MODE == "sequential":
                        turn_number = message["params"]["threadId"].rsplit("-", 1)[1]
                        send({{
                            "method": "item/agentMessage/delta",
                            "params": {{
                                "threadId": message["params"]["threadId"],
                                "turnId": f"turn-{{turn_number}}",
                                "itemId": f"reply-{{turn_number}}",
                                "delta": f"応答 {{turn_number}}",
                            }},
                        }})
                        send({{
                            "method": "turn/completed",
                            "params": {{
                                "threadId": message["params"]["threadId"],
                                "turn": {{"id": f"turn-{{turn_number}}", "status": "completed"}},
                            }},
                        }})
                        continue
                    if MODE == "turn-missing-field":
                        continue
                    if MODE == "turn-rerouted":
                        send({{
                            "method": "model/rerouted",
                            "params": {{
                                "threadId": "thread-1",
                                "turnId": "turn-1",
                                "fromModel": "gpt-5.6-luna",
                                "toModel": "gpt-5.6-luna-rerouted",
                                "reason": "highRiskCyberActivity",
                            }},
                        }})
                        send({{
                            "method": "item/agentMessage/delta",
                            "params": {{
                                "threadId": "thread-1",
                                "turnId": "turn-1",
                                "itemId": "substituted",
                                "delta": "置換された応答",
                            }},
                        }})
                    elif MODE in {{"completion-missing-field", "completion-invalid-status"}}:
                        completed_turn = {{"id": "turn-1", "status": "completed"}}
                        if MODE == "completion-missing-field":
                            del completed_turn["status"]
                        else:
                            completed_turn["status"] = "unknown"
                        send({{
                            "method": "turn/completed",
                            "params": {{"threadId": "thread-1", "turn": completed_turn}},
                        }})
                    elif MODE == "server-request":
                        send({{"id": "approval-1", "method": "item/commandExecution/requestApproval", "params": {{}}}})
                    elif MODE == "secret-env" and os.environ.get("MEETING_SUPPORTER_SECRET_CANARY"):
                        send(
                            {{
                                "method": "item/agentMessage/delta",
                                "params": {{
                                    "threadId": "thread-1",
                                    "turnId": "turn-1",
                                    "itemId": "unsafe",
                                    "delta": CANARY,
                                }},
                            }}
                        )
                        send(
                            {{
                                "method": "turn/completed",
                                "params": {{
                                    "threadId": "thread-1",
                                    "turn": {{"id": "turn-1", "status": "completed"}},
                                }},
                            }}
                        )
                    elif MODE != "pending":
                        send({{"method": "unrecognized/notification", "params": {{"ignored": True}}}})
                        send({{
                            "method": "turn/completed",
                            "params": {{
                                "threadId": "other",
                                "turn": {{"id": "other", "status": "unknown"}},
                            }},
                        }})
                        send(
                            {{
                                "method": "item/agentMessage/delta",
                                "params": {{
                                    "threadId": "other",
                                    "turnId": "other",
                                    "itemId": "foreign",
                                    "delta": "foreign",
                                }},
                            }}
                        )
                        send(
                            {{
                                "method": "item/agentMessage/delta",
                                "params": {{
                                    "threadId": "thread-1",
                                    "turnId": "turn-1",
                                    "itemId": "reply",
                                    "delta": "日本語の応答",
                                }},
                            }}
                        )
                        send(
                            {{
                                "method": "turn/completed",
                                "params": {{
                                    "threadId": "thread-1",
                                    "turn": {{"id": "turn-1", "status": "completed"}},
                                }},
                            }}
                        )
                elif method == "thread/unsubscribe":
                    if MODE == "unsubscribe-exits":
                        raise SystemExit(0)
                    send({{"id": request_id, "result": {{}}}})
                elif method == "turn/interrupt":
                    send({{"id": request_id, "result": {{}}}})
                elif request_id is not None:
                    send({{"id": request_id, "error": {{"code": -32601, "message": "unsupported"}}}})
            """
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        script.chmod(0o700)
    return script, transcript


def _transcript(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def _collect_deltas(
    server: CodexAppServer,
    prompt: str = "日本語で短く答えて",
    model: str = _LUNA,
) -> list[str]:
    turn = await server.begin_reply(prompt, model)
    return [delta async for delta in turn.deltas()]


async def _wait_for_received(transcript: Path, method: str, count: int = 1) -> list[dict[str, Any]]:
    for _ in range(100):
        messages = [
            event["value"]
            for event in _transcript(transcript)
            if event["kind"] == "received" and event["value"].get("method") == method
        ]
        if len(messages) >= count:
            return messages
        await asyncio.sleep(0.01)
    raise AssertionError(f"fake Codex did not receive {count} {method} request(s)")


class _VersionProbeChild:
    """Deterministic child boundary for version-probe cleanup contracts."""

    def __init__(
        self,
        *,
        terminate_raises_process_lookup: bool = False,
        kill_raises_process_lookup: bool = False,
    ) -> None:
        self.returncode: int | None = None
        self.calls: list[str] = []
        self._terminate_raises_process_lookup = terminate_raises_process_lookup
        self._kill_raises_process_lookup = kill_raises_process_lookup

    @staticmethod
    def _completed(value: Any) -> asyncio.Future[Any]:
        result: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        result.set_result(value)
        return result

    def communicate(self) -> asyncio.Future[tuple[bytes, bytes]]:
        self.calls.append("communicate")
        return self._completed((b"", b""))

    def terminate(self) -> None:
        self.calls.append("terminate")
        if self._terminate_raises_process_lookup:
            raise ProcessLookupError

    def kill(self) -> None:
        self.calls.append("kill")
        if self._kill_raises_process_lookup:
            raise ProcessLookupError

    def wait(self) -> asyncio.Future[None]:
        self.calls.append("wait")
        return self._completed(None)


class _VersionProbeTimeoutGate:
    """Returns prescribed timeout outcomes without introducing a wall-clock wait."""

    def __init__(self, outcomes: tuple[bool, ...]) -> None:
        self._outcomes = iter(outcomes)

    async def __call__(self, awaitable: Any, timeout: float | None = None) -> Any:
        _ = timeout
        if next(self._outcomes):
            raise TimeoutError
        return await awaitable


class CodexInstallationContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_classifies_minimum_supported_and_schema_verified_cli_versions(self) -> None:
        """Stable releases at or above the baseline are compatible, while only pinned schemas are verified."""
        cases = (
            ("verified 0.144.0 release", "0.144.0", True, True, None),
            ("verified 0.144.1 release", "0.144.1", True, True, None),
            ("newer unverified patch", "0.144.2", True, False, None),
            ("newer unverified minor release", "0.145.0", True, False, None),
            ("older patch release", "0.143.9", False, False, "unsupported_version"),
            ("malformed version banner", "0.144.1 (dev)", False, False, "unsupported_version"),
            ("prerelease version", "0.145.0-beta.1", False, False, "unsupported_version"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            for name, version, compatible, schema_verified, reason in cases:
                with self.subTest(name=name):
                    binary, _ = _write_fake_codex(Path(temporary), mode="normal", version=version)
                    installation = await inspect_codex_installation(binary)
                    self.assertEqual(compatible, installation.compatible)
                    self.assertEqual(schema_verified, installation.schema_verified)
                    self.assertEqual(reason, installation.reason_code)

    async def test_timeout_reaps_the_version_probe_child_despite_exit_races(self) -> None:
        """A timed-out version probe must reap its child even if it exits during termination."""
        cases = (
            ("terminate then reap", (True, False), False, False, ["communicate", "terminate", "wait"]),
            (
                "kill after termination wait expires",
                (True, True),
                False,
                False,
                ["communicate", "terminate", "wait", "kill", "wait"],
            ),
            (
                "terminate race still reaps",
                (True, False),
                True,
                False,
                ["communicate", "terminate", "wait"],
            ),
            (
                "kill race still reaps",
                (True, True),
                False,
                True,
                ["communicate", "terminate", "wait", "kill", "wait"],
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            binary, _ = _write_fake_codex(Path(temporary), mode="normal")
            for name, timeouts, terminate_race, kill_race, expected_calls in cases:
                with self.subTest(name=name):
                    child = _VersionProbeChild(
                        terminate_raises_process_lookup=terminate_race,
                        kill_raises_process_lookup=kill_race,
                    )
                    timeout_gate = _VersionProbeTimeoutGate(timeouts)

                    async def create_child(*args: Any, **kwargs: Any) -> _VersionProbeChild:
                        _ = (args, kwargs)
                        return child

                    with (
                        patch("app.agents.codex_app_server.asyncio.create_subprocess_exec", create_child),
                        patch("app.agents.codex_app_server.asyncio.wait_for", timeout_gate),
                    ):
                        installation = await inspect_codex_installation(binary)

                    self.assertEqual("version_unavailable", installation.reason_code)
                    self.assertEqual(expected_calls, child.calls)

    async def test_cancellation_reaps_the_version_probe_child(self) -> None:
        """Caller cancellation cannot leave a Windows version-probe process holding its executable."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, _ = _write_fake_codex(Path(temporary), mode="normal")
            child = _VersionProbeChild()
            wait_calls = 0

            async def create_child(*args: Any, **kwargs: Any) -> _VersionProbeChild:
                _ = (args, kwargs)
                return child

            async def cancel_first_wait(awaitable: Any, timeout: float | None = None) -> Any:
                nonlocal wait_calls
                _ = timeout
                wait_calls += 1
                if wait_calls == 1:
                    raise asyncio.CancelledError
                return await awaitable

            with (
                patch("app.agents.codex_app_server.asyncio.create_subprocess_exec", create_child),
                patch("app.agents.codex_app_server.asyncio.wait_for", cancel_first_wait),
                self.assertRaises(asyncio.CancelledError),
            ):
                _ = await inspect_codex_installation(binary)

        self.assertEqual(["communicate", "terminate", "wait"], child.calls)

    async def test_cancellation_during_version_probe_cleanup_still_waits_for_reaping(self) -> None:
        """Cancellation during timeout cleanup is re-raised only after the child reaper completes."""
        child = _VersionProbeChild()
        cleanup_started = asyncio.Event()
        allow_cleanup = asyncio.Event()
        cleanup_completed = False

        async def controlled_cleanup(_child: _VersionProbeChild) -> None:
            nonlocal cleanup_completed
            cleanup_started.set()
            _ = await allow_cleanup.wait()
            cleanup_completed = True

        with patch("app.agents.codex_app_server._terminate_version_probe", controlled_cleanup):
            cleanup = asyncio.create_task(
                _reap_version_probe(child)  # pyright: ignore[reportArgumentType]
            )
            _ = await cleanup_started.wait()
            _ = cleanup.cancel()
            await asyncio.sleep(0)
            allow_cleanup.set()
            with self.assertRaises(asyncio.CancelledError):
                await cleanup

        self.assertTrue(cleanup_completed)


class CodexAppServerContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_initializes_before_announcing_ready_and_reuses_one_process_for_requests(self) -> None:
        """The JSONL peer initializes once, announces initialized second, then serves later
        requests over that process."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="normal")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                await server.ensure_ready()
                first = await server.read_account()
                second = await server.read_account()
                events = _transcript(transcript)
                state_before_close = server.process_state
            finally:
                await server.close()

        received = [event["value"] for event in events if event["kind"] == "received"]
        startup = [event["value"] for event in events if event["kind"] == "startup"]
        self.assertEqual(["initialize", "initialized"], [received[0]["method"], received[1]["method"]])
        self.assertEqual(["account/read", "account/read"], [received[2]["method"], received[3]["method"]])
        self.assertEqual(1, len(startup))
        self.assertTrue(first.authenticated)
        self.assertTrue(second.authenticated)
        self.assertEqual(ProcessState.READY, state_before_close)

    @unittest.skipUnless(os.name == "nt", "Windows directory handles are released asynchronously")
    async def test_close_retries_temporary_directory_cleanup_after_windows_handle_release(self) -> None:
        """Closing a real Windows peer tolerates the short delay before its cwd can be removed."""
        server = CodexAppServer()
        temporary_directory = Mock()
        temporary_directory.cleanup.side_effect = [PermissionError, None]
        server._temporary_directory = temporary_directory

        with patch("app.agents.codex_app_server.asyncio.sleep", new=AsyncMock()) as sleep:
            await server._cleanup_temporary_directory()

        self.assertEqual(2, temporary_directory.cleanup.call_count)
        sleep.assert_awaited_once_with(0.05)

    async def test_newer_stable_cli_becomes_ready_after_non_billable_protocol_probe(self) -> None:
        """A newer stable CLI is selectable after typed initialization, account, and model checks without a turn."""
        for version in ("0.144.2", "0.145.0"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temporary:
                binary, transcript = _write_fake_codex(Path(temporary), mode="normal", version=version)
                server = CodexAppServer(binary=binary, work_root=temporary)
                try:
                    readiness = await probe_codex_route_status(server, _LUNA)
                    events = _transcript(transcript)
                finally:
                    await server.close()

                received_methods = [event["value"]["method"] for event in events if event["kind"] == "received"]
                self.assertEqual("ready", readiness.readiness)
                self.assertEqual("untested_newer_version", readiness.reason_code)
                self.assertEqual(["initialize", "initialized"], received_methods[:2])
                self.assertIn("account/read", received_methods)
                self.assertIn("model/list", received_methods)
                self.assertLess(received_methods.index("account/read"), received_methods.index("model/list"))
                self.assertNotIn("thread/start", received_methods)
                self.assertNotIn("turn/start", received_methods)

    async def test_missing_required_protocol_fields_fail_closed_before_any_turn_starts(self) -> None:
        """Each required response boundary rejects omitted fields as a non-retryable protocol incompatibility."""
        cases = (
            ("initialize response", "initialize-missing-field", "initialize"),
            ("account response", "account-missing-field", "account"),
            ("model response", "model-missing-field", "model"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            for name, mode, boundary in cases:
                with self.subTest(name=name):
                    binary, transcript = _write_fake_codex(Path(temporary), mode=mode)
                    server = CodexAppServer(binary=binary, work_root=temporary)
                    try:
                        with self.assertRaises(CodexSafeError) as raised:
                            if boundary == "initialize":
                                await server.ensure_ready()
                            elif boundary == "account":
                                _ = await server.read_account()
                            else:
                                _ = await server.model_service_tier(_LUNA, effort="low")
                        events = _transcript(transcript)
                    finally:
                        await server.close()

                    received_methods = [event["value"]["method"] for event in events if event["kind"] == "received"]
                    self.assertEqual("protocol_incompatible", raised.exception.code)
                    self.assertFalse(raised.exception.retryable)
                    self.assertNotIn("thread/start", received_methods)
                    self.assertNotIn("turn/start", received_methods)

    async def test_authentication_state_distinguishes_missing_login_authenticated_account_and_login_start(self) -> None:
        """Account results and login initiation drive the externally surfaced authentication state machine."""
        with tempfile.TemporaryDirectory() as temporary:
            unauth_binary, _ = _write_fake_codex(Path(temporary), mode="unauthenticated")
            unauthenticated = CodexAppServer(binary=unauth_binary, work_root=temporary)
            try:
                missing_account = await unauthenticated.read_account()
                self.assertFalse(missing_account.authenticated)
                self.assertEqual(AuthState.UNAUTHENTICATED, unauthenticated.auth_state)
            finally:
                await unauthenticated.close()

            binary, _ = _write_fake_codex(Path(temporary), mode="auth")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                authenticated_account = await server.read_account()
                login = await server.start_login()
                self.assertTrue(authenticated_account.authenticated)
                self.assertEqual("login-1", login.login_id)
                self.assertEqual(AuthState.LOGGING_IN, server.auth_state)
                _ = await server.request("probe/login-completion", {})
                self.assertEqual(AuthState.UNAUTHENTICATED, server.auth_state)
            finally:
                await server.close()

    async def test_routes_only_matching_stream_events_and_ignores_unknown_notifications(self) -> None:
        """A reply stream emits only the active thread/turn's deltas, despite unrelated protocol traffic."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="normal")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                deltas = await _collect_deltas(server)
                events = _transcript(transcript)
            finally:
                await server.close()

        self.assertEqual(["日本語の応答"], deltas)
        thread_start = next(
            event["value"]
            for event in events
            if event["kind"] == "received" and event["value"]["method"] == "thread/start"
        )
        parameters = thread_start["params"]
        self.assertEqual("never", parameters["approvalPolicy"])
        self.assertEqual("read-only", parameters["sandbox"])
        self.assertTrue(parameters["ephemeral"])
        self.assertEqual({}, parameters["config"]["mcp_servers"])
        self.assertEqual([], parameters["config"]["skills"]["config"])
        self.assertEqual(False, parameters["config"]["tools"]["web_search"])
        self.assertEqual(False, parameters["config"]["tools"]["view_image"])
        self.assertNotIn("foreign", "".join(deltas))

    async def test_luna_turn_payload_uses_low_effort_without_service_tier_and_keeps_effective_model(self) -> None:
        """A configured Luna turn sends one low-latency model contract and retains the server's effective model."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="normal")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                turn = await server.begin_reply("要点だけ答えて", _LUNA)
                deltas = [delta async for delta in turn.deltas()]
                events = _transcript(transcript)
                selection = server.last_model_selection
            finally:
                await server.close()

        requests = [event["value"] for event in events if event["kind"] == "received"]
        thread_start = next(request for request in requests if request["method"] == "thread/start")
        turn_start = next(request for request in requests if request["method"] == "turn/start")
        self.assertEqual(["日本語の応答"], deltas)
        self.assertEqual(_LUNA, thread_start["params"]["model"])
        self.assertEqual(_LUNA, turn_start["params"]["model"])
        self.assertEqual("low", turn_start["params"]["effort"])
        self.assertNotIn("serviceTier", thread_start["params"])
        self.assertNotIn("serviceTier", turn_start["params"])
        if selection is None:
            self.fail("completed standard turn must record its model selection")
        self.assertEqual("standard", selection.service_tier)
        self.assertIsNone(selection.requested_service_tier)
        self.assertIsNone(selection.effective_service_tier)
        self.assertEqual(_LUNA, turn.requested_model)
        self.assertEqual(_LUNA, turn.effective_model)
        self.assertEqual("chatgpt", turn.effective_model_provider)
        self.assertEqual("low", turn.reasoning_effort)

    async def test_priority_catalog_requests_and_observes_priority_service_tier(self) -> None:
        """An advertised priority tier is requested at thread creation and recorded from the typed server response."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="priority")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                turn = await server.begin_reply("要点だけ答えて", _LUNA)
                deltas = [delta async for delta in turn.deltas()]
                selection = server.last_model_selection
                events = _transcript(transcript)
            finally:
                await server.close()

        thread_start = next(
            event["value"]
            for event in events
            if event["kind"] == "received" and event["value"].get("method") == "thread/start"
        )
        self.assertEqual(["日本語の応答"], deltas)
        self.assertEqual("priority", thread_start["params"]["serviceTier"])
        if selection is None:
            self.fail("completed priority turn must record its model selection")
        self.assertEqual("priority", selection.service_tier)
        self.assertEqual("priority", selection.requested_service_tier)
        self.assertEqual("priority", selection.effective_service_tier)

    async def test_priority_fallback_is_observed_safely_without_blocking_the_turn(self) -> None:
        """A priority-to-standard fallback remains usable while exposing its actual effective service tier."""
        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            @override
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        logger = logging.getLogger("app.agents.codex_app_server")
        handler = _Capture()
        logger.addHandler(handler)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                binary, transcript = _write_fake_codex(Path(temporary), mode="priority-standard")
                server = CodexAppServer(binary=binary, work_root=temporary)
                advertised_tier = await server.model_service_tier(_LUNA, effort="low")
                try:
                    turn = await server.begin_reply("要点だけ答えて", _LUNA)
                    deltas = [delta async for delta in turn.deltas()]
                    selection = server.last_model_selection
                    events = _transcript(transcript)
                finally:
                    await server.close()
        finally:
            logger.removeHandler(handler)

        received_methods = [event["value"]["method"] for event in events if event["kind"] == "received"]
        log_text = "\n".join(record.getMessage() for record in records)
        self.assertEqual(["日本語の応答"], deltas)
        self.assertIn("turn/start", received_methods)
        self.assertEqual("priority", advertised_tier)
        if selection is None:
            self.fail("completed fallback turn must record its model selection")
        self.assertEqual("priority", selection.service_tier)
        self.assertEqual("priority", selection.requested_service_tier)
        self.assertEqual("standard", selection.effective_service_tier)
        self.assertIn("service tier fallback", log_text)

    async def test_rejects_a_thread_model_reroute_before_starting_a_substituted_turn(self) -> None:
        """A Codex-selected model different from Luna must fail safely before any turn can
        produce substituted output."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="thread-rerouted")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                with self.assertRaises(CodexSafeError) as raised:
                    _ = await server.begin_reply("要点だけ答えて", _LUNA)
                events = _transcript(transcript)
            finally:
                await server.close()

        self.assertEqual("model_rerouted", raised.exception.code)
        received_methods = [event["value"]["method"] for event in events if event["kind"] == "received"]
        self.assertIn("thread/start", received_methods)
        self.assertNotIn("turn/start", received_methods)

    async def test_malformed_start_results_fail_closed_and_terminate_the_uncertain_peer(self) -> None:
        """Missing required ids after thread/start or turn/start cannot leave the app-server alive."""
        cases = (
            ("thread/start", "thread-missing-field", False),
            ("turn/start", "turn-missing-field", True),
        )
        for boundary, mode, turn_started in cases:
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                binary, transcript = _write_fake_codex(Path(temporary), mode=mode)
                server = CodexAppServer(binary=binary, work_root=temporary)
                try:
                    with self.assertRaises(CodexSafeError) as raised:
                        _ = await server.begin_reply("要点だけ答えて", _LUNA)
                    events = _transcript(transcript)
                    process_state_after_failure = server.process_state
                    turn_state_after_failure = server.turn_state
                    with self.assertRaises(CodexSafeError) as cwd_error:
                        _ = server.cwd
                finally:
                    await server.close()

            received_methods = [event["value"]["method"] for event in events if event["kind"] == "received"]
            self.assertEqual("protocol_incompatible", raised.exception.code)
            self.assertFalse(raised.exception.retryable)
            self.assertEqual(ProcessState.FAILED, process_state_after_failure)
            self.assertEqual(TurnState.IDLE, turn_state_after_failure)
            self.assertEqual("runtime_not_ready", cwd_error.exception.code)
            self.assertIn("thread/start", received_methods)
            if turn_started:
                self.assertIn("turn/start", received_methods)
            else:
                self.assertNotIn("turn/start", received_methods)

    async def test_active_turn_reroute_fails_and_discards_substituted_output(self) -> None:
        """A model/rerouted notification terminates the active stream before a replacement model can emit text."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, _ = _write_fake_codex(Path(temporary), mode="turn-rerouted")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                turn = await server.begin_reply("要点だけ答えて", _LUNA)
                deltas: list[str] = []
                with self.assertRaises(CodexSafeError) as raised:
                    async for delta in turn.deltas():
                        deltas.append(delta)
                state_after_reroute = server.turn_state
            finally:
                await server.close()

        self.assertEqual("model_rerouted", raised.exception.code)
        self.assertEqual([], deltas)
        self.assertTrue(turn.finished)
        self.assertEqual(TurnState.IDLE, state_after_reroute)

    async def test_active_malformed_completion_fails_closed_and_terminates_the_peer(self) -> None:
        """Malformed completion notifications for the active turn cannot leave its peer or stream usable."""
        cases = (
            ("missing required completion status", "completion-missing-field"),
            ("invalid completion status", "completion-invalid-status"),
        )
        for name, mode in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                binary, transcript = _write_fake_codex(Path(temporary), mode=mode)
                server = CodexAppServer(binary=binary, work_root=temporary)
                try:
                    turn = await server.begin_reply("要点だけ答えて", _LUNA)
                    deltas: list[str] = []
                    with self.assertRaises(CodexSafeError) as raised:
                        async for delta in turn.deltas():
                            deltas.append(delta)
                    events = _transcript(transcript)
                    process_state_after_failure = server.process_state
                    turn_state_after_failure = server.turn_state
                    with self.assertRaises(CodexSafeError) as cwd_error:
                        _ = server.cwd
                finally:
                    await server.close()

            received_methods = [event["value"]["method"] for event in events if event["kind"] == "received"]
            self.assertEqual("protocol_incompatible", raised.exception.code)
            self.assertFalse(raised.exception.retryable)
            self.assertEqual([], deltas)
            self.assertTrue(turn.finished)
            self.assertEqual(ProcessState.FAILED, process_state_after_failure)
            self.assertEqual(TurnState.IDLE, turn_state_after_failure)
            self.assertEqual("runtime_not_ready", cwd_error.exception.code)
            self.assertIn("turn/start", received_methods)

    async def test_runtime_propagates_its_configured_luna_model_to_the_app_server(self) -> None:
        """The public reply runtime must stream using its configured route model rather than a hidden fallback."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="normal")
            server = CodexAppServer(binary=binary, work_root=temporary)
            runtime = CodexReplyAgentRuntime(peer=server, model=_LUNA)
            try:
                async with runtime.run_stream(ReplyPrompt(text="短く答えて")) as stream:
                    deltas = [delta async for delta in stream.stream_text(delta=True)]
                events = _transcript(transcript)
            finally:
                await server.close()

        turn_start = next(
            event["value"]
            for event in events
            if event["kind"] == "received" and event["value"]["method"] == "turn/start"
        )
        self.assertEqual(["日本語の応答"], deltas)
        self.assertEqual(_LUNA, turn_start["params"]["model"])

    async def test_info_runtime_starts_a_read_only_tool_disabled_complete_note_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="normal")
            server = CodexAppServer(binary=binary, work_root=temporary)
            runtime = CodexInfoAgentRuntime(peer=server, model=_LUNA)
            try:
                async with runtime.run_stream(InfoPrompt(text="現在のメモと会話")) as stream:
                    deltas = [delta async for delta in stream.stream_text(delta=True)]
                _ = await _wait_for_received(transcript, "thread/unsubscribe")
                events = _transcript(transcript)
            finally:
                await server.close()

        thread_start = next(
            event["value"]
            for event in events
            if event["kind"] == "received" and event["value"]["method"] == "thread/start"
        )
        methods = [event["value"]["method"] for event in events if event["kind"] == "received"]
        config = thread_start["params"]["config"]
        self.assertEqual("complete_note", runtime.output_mode)
        self.assertEqual(["日本語の応答"], deltas)
        self.assertEqual(_LUNA, thread_start["params"]["model"])
        self.assertIn(CODEX_INFO_INSTRUCTION, thread_start["params"]["baseInstructions"])
        self.assertEqual(thread_start["params"]["baseInstructions"], thread_start["params"]["developerInstructions"])
        self.assertEqual("never", thread_start["params"]["approvalPolicy"])
        self.assertEqual("read-only", thread_start["params"]["sandbox"])
        self.assertEqual({}, config["mcp_servers"])
        self.assertEqual({"view_image": False, "web_search": False}, config["tools"])
        self.assertEqual("disabled", config["web_search"])
        self.assertEqual(False, config["features"]["shell_tool"])
        self.assertIn("thread/unsubscribe", methods)

    async def test_minutes_runtime_starts_a_read_only_tool_disabled_minutes_turn(self) -> None:
        """Minutes generation is isolated from reply instructions and cannot start a tool-capable Codex turn."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="normal")
            server = CodexAppServer(binary=binary, work_root=temporary)
            runtime = CodexMinutesAgentRuntime(peer=server, model=_LUNA)
            try:
                async with runtime.run_stream(MinutesPrompt(text="会議の書き起こし")) as stream:
                    deltas = [delta async for delta in stream.stream_text(delta=True)]
                events = _transcript(transcript)
            finally:
                await server.close()

        thread_start = next(
            event["value"]
            for event in events
            if event["kind"] == "received" and event["value"]["method"] == "thread/start"
        )
        config = thread_start["params"]["config"]
        self.assertEqual(["日本語の応答"], deltas)
        self.assertIn(MINUTES_INSTRUCTION, thread_start["params"]["baseInstructions"])
        self.assertEqual(thread_start["params"]["baseInstructions"], thread_start["params"]["developerInstructions"])
        self.assertEqual("never", thread_start["params"]["approvalPolicy"])
        self.assertEqual("read-only", thread_start["params"]["sandbox"])
        self.assertEqual({}, config["mcp_servers"])
        self.assertEqual({"view_image": False, "web_search": False}, config["tools"])
        self.assertEqual("disabled", config["web_search"])

    async def test_closing_an_unfinished_minutes_stream_interrupts_its_turn(self) -> None:
        """Disconnecting the minutes response closes its Codex context rather than retaining a live turn."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="pending")
            server = CodexAppServer(binary=binary, work_root=temporary)
            runtime = CodexMinutesAgentRuntime(peer=server, model=_LUNA)
            try:
                async with runtime.run_stream(MinutesPrompt(text="中断する議事録")):
                    pass
                events = _transcript(transcript)
            finally:
                await server.close()

        interrupts = [
            event["value"]
            for event in events
            if event["kind"] == "received" and event["value"].get("method") == "turn/interrupt"
        ]
        self.assertEqual([{"threadId": "thread-1", "turnId": "turn-1"}], [message["params"] for message in interrupts])

    async def test_unauthenticated_route_readiness_does_not_ask_the_peer_for_models(self) -> None:
        """Route readiness returns login setup before it sends model/list to an unauthenticated Codex peer."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="unauthenticated")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                readiness = await probe_codex_route_status(server, _LUNA)
                events = _transcript(transcript)
            finally:
                await server.close()

        self.assertEqual("setup_required", readiness.readiness)
        self.assertEqual("not_logged_in", readiness.reason_code)
        received_methods = [event["value"]["method"] for event in events if event["kind"] == "received"]
        self.assertNotIn("model/list", received_methods)

    async def test_denies_server_approval_requests_and_interrupts_the_exposed_turn(self) -> None:
        """A server-originated capability request is cancelled and cannot leave a tool-capable turn active."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="server-request")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                deltas = await asyncio.wait_for(_collect_deltas(server), timeout=5)
                events = _transcript(transcript)
            finally:
                await server.close()

        messages = [event["value"] for event in events if event["kind"] == "received"]
        denial = next(message for message in messages if message.get("id") == "approval-1")
        interrupt = next(message for message in messages if message.get("method") == "turn/interrupt")
        self.assertEqual({"decision": "cancel"}, denial["result"])
        self.assertEqual("thread-1", interrupt["params"]["threadId"])
        self.assertEqual("turn-1", interrupt["params"]["turnId"])
        self.assertEqual([], deltas)
        self.assertEqual(TurnState.IDLE, server.turn_state)

    async def test_malformed_protocol_and_eof_fail_with_safe_errors_and_cleanup(self) -> None:
        """Broken peer output and peer EOF surface stable safe errors instead of raw protocol data."""
        for mode, expected_code in (("malformed", "protocol_error"), ("eof", "process_exited")):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                binary, _ = _write_fake_codex(Path(temporary), mode=mode)
                server = CodexAppServer(binary=binary, work_root=temporary)
                try:
                    with self.assertRaises(CodexSafeError) as raised:
                        _ = await server.read_account()
                    self.assertEqual(expected_code, raised.exception.code)
                    self.assertNotIn(_CANARY, str(raised.exception))
                finally:
                    await server.close()

    async def test_request_timeout_is_safe_and_does_not_leave_a_pending_operation(self) -> None:
        """A non-responsive peer returns the retryable timeout contract rather than retaining a request forever."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, _ = _write_fake_codex(Path(temporary), mode="timeout")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                with self.assertRaises(CodexSafeError) as raised:
                    _ = await server.request("account/read", {}, timeout=0.02)
                self.assertEqual("request_timeout", raised.exception.code)
                self.assertTrue(raised.exception.retryable)
                self.assertEqual({}, server._pending)
            finally:
                await server.close()

    async def test_interrupt_finishes_an_active_turn_and_sends_its_exact_correlation_ids(self) -> None:
        """Cancellation addresses the active thread/turn pair and releases the server for a later request."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="pending")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                turn = await server.begin_reply("中断して", _LUNA)
                await turn.interrupt()
                events = _transcript(transcript)
            finally:
                await server.close()

        interrupts = [
            event["value"]
            for event in events
            if event["kind"] == "received" and event["value"].get("method") == "turn/interrupt"
        ]
        self.assertEqual(1, len(interrupts))
        self.assertEqual({"threadId": "thread-1", "turnId": "turn-1"}, interrupts[0]["params"])
        self.assertTrue(turn.finished)

    async def test_retains_at_most_the_bounded_stderr_tail_and_never_exposes_it(self) -> None:
        """An untrusted peer cannot turn stderr into unbounded retained memory or user-visible secret text."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, _ = _write_fake_codex(Path(temporary), mode="stderr")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                with self.assertRaises(CodexSafeError) as raised:
                    _ = await server.request("not/a/real/method", {})
                self.assertEqual("request_rejected", raised.exception.code)
                self.assertLessEqual(server._stderr_size, 64 * 1024)
                self.assertNotIn(_CANARY, str(raised.exception))
            finally:
                await server.close()

    async def test_drops_arbitrary_parent_secret_environment_before_a_fake_can_exfiltrate_it(self) -> None:
        """A fake peer that echoes an inherited secret cannot receive it or expose it in reply
        output or application logs."""
        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            @override
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        logger = logging.getLogger("app.agents.codex_app_server")
        handler = _Capture()
        logger.addHandler(handler)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                binary, transcript = _write_fake_codex(root, mode="secret-env")
                server = CodexAppServer(binary=binary, work_root=root)
                with patch.dict(os.environ, {"MEETING_SUPPORTER_SECRET_CANARY": _CANARY}, clear=False):
                    try:
                        deltas = await _collect_deltas(server)
                        events = _transcript(transcript)
                    finally:
                        await server.close()
        finally:
            logger.removeHandler(handler)

        startup = next(event["value"] for event in events if event["kind"] == "startup")
        log_text = "\n".join(record.getMessage() for record in records)
        self.assertIsNone(startup["secretCanary"])
        self.assertEqual(["日本語の応答"], deltas)
        self.assertNotIn(_CANARY, "".join(deltas))
        self.assertNotIn(_CANARY, log_text)

    async def test_unsubscribes_the_typed_created_thread_after_terminal_completion(self) -> None:
        """Terminal completion releases its ephemeral subscription after the terminal notification."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="normal")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                deltas = await _collect_deltas(server)
                unsubscribes = await _wait_for_received(transcript, "thread/unsubscribe")
                events = _transcript(transcript)
            finally:
                await server.close()

        self.assertEqual(["日本語の応答"], deltas)
        self.assertEqual([{"threadId": "thread-1"}], [request["params"] for request in unsubscribes])
        terminal_index = next(
            index
            for index, event in enumerate(events)
            if event["kind"] == "sent"
            and event["value"].get("method") == "turn/completed"
            and event["value"]["params"]["threadId"] == "thread-1"
        )
        unsubscribe_index = next(
            index
            for index, event in enumerate(events)
            if event["kind"] == "received" and event["value"].get("method") == "thread/unsubscribe"
        )
        self.assertLess(terminal_index, unsubscribe_index)

    async def test_unsubscribes_once_after_interrupt_and_consumer_cancellation(self) -> None:
        """Explicit interruption and an abandoning consumer each release their one created thread."""
        for case in ("interrupt", "consumer cancellation"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                binary, transcript = _write_fake_codex(Path(temporary), mode="pending")
                server = CodexAppServer(binary=binary, work_root=temporary)
                try:
                    turn = await server.begin_reply("中断して", _LUNA)
                    if case == "interrupt":
                        await turn.interrupt()
                    else:
                        stream = turn.deltas()

                        async def consume_next_delta() -> str:
                            return await anext(stream)

                        consumer = asyncio.create_task(consume_next_delta())
                        await asyncio.sleep(0)
                        _ = consumer.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            _ = await consumer
                    unsubscribes = await _wait_for_received(transcript, "thread/unsubscribe")
                finally:
                    await server.close()

                self.assertTrue(turn.finished)
                self.assertEqual([{"threadId": "thread-1"}], [request["params"] for request in unsubscribes])

    async def test_unsubscribes_once_when_a_created_thread_later_fails(self) -> None:
        """Every failure after thread/start releases that thread, including errors before a turn exists."""
        cases = (
            ("turn start protocol result", "turn-missing-field", "protocol_incompatible", True),
            ("thread model reroute", "thread-rerouted", "model_rerouted", True),
            ("terminal protocol notification", "completion-missing-field", "protocol_incompatible", False),
        )
        for name, mode, error_code, fails_during_start in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                binary, transcript = _write_fake_codex(Path(temporary), mode=mode)
                server = CodexAppServer(binary=binary, work_root=temporary)
                try:
                    with self.assertRaises(CodexSafeError) as raised:
                        if fails_during_start:
                            _ = await server.begin_reply("要点だけ答えて", _LUNA)
                        else:
                            turn = await server.begin_reply("要点だけ答えて", _LUNA)
                            _ = [delta async for delta in turn.deltas()]
                    unsubscribes = await _wait_for_received(transcript, "thread/unsubscribe")
                finally:
                    await server.close()

                self.assertEqual(error_code, raised.exception.code)
                self.assertEqual([{"threadId": "thread-1"}], [request["params"] for request in unsubscribes])

    async def test_sequential_threads_release_their_own_subscription_once(self) -> None:
        """Later replies cannot reuse or double-release the subscription belonging to an earlier reply."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="sequential")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                first = await _collect_deltas(server, "最初の応答")
                second = await _collect_deltas(server, "次の応答")
                unsubscribes = await _wait_for_received(transcript, "thread/unsubscribe", count=2)
                events = _transcript(transcript)
            finally:
                await server.close()

        self.assertEqual(["応答 1"], first)
        self.assertEqual(["応答 2"], second)
        self.assertEqual(
            [{"threadId": "thread-1"}, {"threadId": "thread-2"}],
            [request["params"] for request in unsubscribes],
        )
        for thread_id in ("thread-1", "thread-2"):
            terminal_index = next(
                index
                for index, event in enumerate(events)
                if event["kind"] == "sent"
                and event["value"].get("method") == "turn/completed"
                and event["value"]["params"]["threadId"] == thread_id
            )
            unsubscribe_index = next(
                index
                for index, event in enumerate(events)
                if event["kind"] == "received"
                and event["value"].get("method") == "thread/unsubscribe"
                and event["value"]["params"] == {"threadId": thread_id}
            )
            self.assertLess(terminal_index, unsubscribe_index)

    async def test_peer_exit_while_unsubscribing_does_not_repeat_cleanup(self) -> None:
        """A peer that exits during cleanup has already observed one typed unsubscribe request."""
        with tempfile.TemporaryDirectory() as temporary:
            binary, transcript = _write_fake_codex(Path(temporary), mode="unsubscribe-exits")
            server = CodexAppServer(binary=binary, work_root=temporary)
            try:
                deltas = await _collect_deltas(server)
                unsubscribes = await _wait_for_received(transcript, "thread/unsubscribe")
                await asyncio.sleep(0.01)
            finally:
                await server.close()

        self.assertEqual(["日本語の応答"], deltas)
        self.assertEqual([{"threadId": "thread-1"}], [request["params"] for request in unsubscribes])


class CodexLiveSmokeTest(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(
        os.environ.get("RUN_CODEX_LIVE_SMOKE") == "1",
        "set RUN_CODEX_LIVE_SMOKE=1 to use the current user's existing Codex login",
    )
    async def test_opt_in_account_read_japanese_turn_and_cancel(self) -> None:
        """The explicit live smoke only reads the existing account, starts one Japanese reply, then cancels it."""
        server = CodexAppServer(binary=os.environ.get("CODEX_BINARY"))
        try:
            account = await server.read_account()
            if not account.authenticated:
                self.skipTest("the explicit live smoke requires an existing ChatGPT Codex login")
            turn = await server.begin_reply("日本語で一文だけ応答してください。", _LUNA)
            cancelled = await server.interrupt_active_turn()
            self.assertTrue(cancelled)
            self.assertTrue(turn.finished)
        finally:
            await server.close()


if __name__ == "__main__":
    _ = unittest.main()
