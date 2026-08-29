"""Codex CLI discovery, launch inputs, and installation inspection."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from asyncio.subprocess import PIPE, Process
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_CODEX_VERSION = re.compile(r"^codex-cli\s+(\S+)$")
_RELEASE_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_MINIMUM_CODEX_VERSION: Final = (0, 144, 0)
_SCHEMA_VERIFIED_CODEX_VERSIONS: Final[frozenset[str]] = frozenset({"0.144.0", "0.144.1", "0.151.0"})
MINIMUM_CODEX_VERSION_LABEL: Final = "0.144.0 以降"

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
        "SYSTEMROOT",
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


@dataclass(frozen=True)
class CodexInstallation:
    binary: Path | None
    version: str | None
    compatible: bool
    schema_verified: bool
    reason_code: str | None


def child_environment(parent_environment: Mapping[str, str]) -> dict[str, str]:
    """Pass only authentication, connectivity, locale, and temporary-path inputs to Codex."""

    return {
        key: value
        for key, value in parent_environment.items()
        if key in _CHILD_ENVIRONMENT_VARIABLES or key.startswith("LC_")
    }


def _parse_release_version(value: str | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    match = _RELEASE_VERSION.fullmatch(value)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def codex_process_command(binary: Path, *arguments: str) -> tuple[str, ...]:
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
            *codex_process_command(path, "--version"),
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
