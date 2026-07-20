#!/usr/bin/env python3
"""Audit distributable dependencies and generate THIRD-PARTY-NOTICES.txt."""

from __future__ import annotations

import argparse
import csv
import email.policy
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from dataclasses import dataclass, field
from email.parser import BytesParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "licenses" / "policy.json"
OUTPUT_PATH = ROOT / "THIRD-PARTY-NOTICES.txt"
LICENSE_NAME = re.compile(
    r"^(?:licen[cs]es?|copying|copyright|notices?|authors?|third[-_ ]?party[-_ ]?(?:licenses?|notices?))(?:[._-].*)?$",
    re.IGNORECASE,
)
REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)")
REPOSITORY_LICENSE_CACHE: dict[tuple[str, str], list[tuple[str, str]]] = {}
TOKEN = re.compile(r"\s*(\(|\)|AND\b|OR\b|WITH\b|[A-Za-z0-9.+-]+)")


@dataclass
class Package:
    ecosystem: str
    name: str
    version: str
    license_expression: str
    source: str
    attribution: str = ""
    repository: str = ""
    source_commit: str = ""
    documents: list[tuple[str, str]] = field(default_factory=list)

    @property
    def reference(self) -> str:
        return f"{self.ecosystem}:{self.name}@{self.version}"


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed:\n{result.stderr.strip()}")
    return result.stdout


def clean_text(data: bytes) -> str | None:
    if b"\0" in data or len(data) > 512 * 1024:
        return None
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()
    return text if len(text) >= 40 else None


def root_license_documents(
    directory: Path, explicit: list[Path] | None = None
) -> list[tuple[str, str]]:
    candidates = list(explicit or [])
    if directory.is_dir():
        candidates.extend(
            path
            for path in directory.iterdir()
            if path.is_file() and LICENSE_NAME.match(path.name)
        )
    documents: list[tuple[str, str]] = []
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            text = clean_text(path.read_bytes())
        except OSError:
            continue
        if text:
            documents.append((path.name, text))
    return sorted(documents)


def repository_url(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("url")
    if not isinstance(value, str) or not value:
        return None
    return re.sub(r"^(?:git\+|git://)", "https://", value).removesuffix(".git")


def npm_packages() -> list[Package]:
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    packages: list[Package] = []
    for relative_path, locked in lock["packages"].items():
        if not relative_path.startswith("node_modules/") or locked.get("dev") is True:
            continue
        directory = ROOT / relative_path
        manifest_path = directory / "package.json"
        if not manifest_path.is_file():
            raise RuntimeError(
                f"npm package is not installed: {relative_path}; run npm ci"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        name = manifest.get("name")
        version = locked.get("version") or manifest.get("version")
        expression = locked.get("license") or manifest.get("license")
        if not all(
            isinstance(value, str) and value for value in (name, version, expression)
        ):
            raise RuntimeError(f"npm package metadata is incomplete: {relative_path}")
        packages.append(
            Package(
                ecosystem="npm",
                name=name,
                version=version,
                license_expression=expression,
                source=locked.get("resolved")
                or f"https://www.npmjs.com/package/{name}/v/{version}",
                attribution=json.dumps(manifest.get("author"), ensure_ascii=False)
                if manifest.get("author")
                else "",
                repository=repository_url(manifest.get("repository")) or "",
                documents=root_license_documents(directory),
            )
        )
    return packages


def cargo_packages(
    project_dir: Path = ROOT / "src-tauri",
    ecosystem: str = "cargo",
    root_names: list[str] | None = None,
    features: list[str] | None = None,
    include_roots: bool = False,
    workspace_source: str | None = None,
) -> list[Package]:
    metadata_command = ["cargo", "metadata", "--locked", "--format-version", "1"]
    if features:
        metadata_command.extend(("--features", ",".join(features)))
    metadata = json.loads(run(metadata_command, project_dir))
    if root_names:
        resolved_roots = set(root_names)
    else:
        root_id = metadata["resolve"]["root"]
        if not root_id:
            raise RuntimeError(f"Cargo project has no root package: {project_dir}")
        root_package = next(
            package for package in metadata["packages"] if package["id"] == root_id
        )
        resolved_roots = {root_package["name"]}

    available_names = {package["name"] for package in metadata["packages"]}
    missing = resolved_roots - available_names
    if missing:
        raise RuntimeError(
            f"Cargo roots not found in {project_dir}: {', '.join(sorted(missing))}"
        )

    distribution_targets = (
        "x86_64-unknown-linux-gnu",
        "x86_64-pc-windows-msvc",
        "x86_64-apple-darwin",
        "aarch64-apple-darwin",
    )
    selected_keys: set[tuple[str, str]] = set()
    for root_name in sorted(resolved_roots):
        for target in distribution_targets:
            tree_command = [
                "cargo",
                "tree",
                "--locked",
                "--edges",
                "normal",
                "--prefix",
                "none",
                "--format",
                "{p}",
                "--package",
                root_name,
                "--target",
                target,
            ]
            if features:
                tree_command.extend(("--features", ",".join(features)))
            for line in run(tree_command, project_dir).splitlines():
                match = re.match(r"^(\S+) v([^\s]+)", line)
                if match:
                    selected_keys.add((match.group(1), match.group(2)))

    root_versions = {
        (package["name"], package["version"])
        for package in metadata["packages"]
        if package["name"] in resolved_roots
    }
    if not include_roots:
        selected_keys -= root_versions

    workspace_root = Path(metadata["workspace_root"])
    packages: list[Package] = []
    selected_metadata = [
        package
        for package in metadata["packages"]
        if (package["name"], package["version"]) in selected_keys
    ]
    for metadata_package in sorted(
        selected_metadata, key=lambda package: package["id"]
    ):
        directory = Path(metadata_package["manifest_path"]).parent
        explicit = []
        if metadata_package.get("license_file"):
            explicit.append(directory / metadata_package["license_file"])
        declared_license = metadata_package.get("license") or ""
        for identifier in TOKEN.findall(declared_license):
            if identifier in {"AND", "OR", "WITH", "(", ")"}:
                continue
            explicit.extend(directory.glob(f"{identifier}.*"))
        documents = root_license_documents(directory, explicit)
        if not documents and directory.is_relative_to(workspace_root):
            documents = root_license_documents(workspace_root)
        cargo_source = metadata_package.get("source") or ""
        if cargo_source.startswith("registry+"):
            source = f"https://crates.io/api/v1/crates/{metadata_package['name']}/{metadata_package['version']}/download"
        elif cargo_source.startswith("git+"):
            source = cargo_source.removeprefix("git+")
        else:
            source = (
                workspace_source or metadata_package.get("repository") or str(directory)
            )
        vcs_info_path = directory / ".cargo_vcs_info.json"
        source_commit = ""
        if vcs_info_path.is_file():
            source_commit = (
                json.loads(vcs_info_path.read_text(encoding="utf-8"))
                .get("git", {})
                .get("sha1", "")
            )
        packages.append(
            Package(
                ecosystem=ecosystem,
                name=metadata_package["name"],
                version=metadata_package["version"],
                license_expression=metadata_package.get("license") or "UNKNOWN",
                source=source,
                repository=metadata_package.get("repository") or "",
                source_commit=source_commit,
                attribution=", ".join(metadata_package.get("authors") or []),
                documents=documents,
            )
        )
    return packages


def python_site_packages() -> list[Path]:
    venv = ROOT / "python" / ".venv"
    candidates = list((venv / "lib").glob("python*/site-packages"))
    candidates.extend((venv / "Lib").glob("site-packages"))
    return [path for path in candidates if path.is_dir()]


def python_metadata() -> dict[str, tuple[Any, Path]]:
    result: dict[str, tuple[Any, Path]] = {}
    for site_packages in python_site_packages():
        for dist_info in site_packages.glob("*.dist-info"):
            metadata_path = dist_info / "METADATA"
            if not metadata_path.is_file():
                continue
            metadata = BytesParser(policy=email.policy.compat32).parsebytes(
                metadata_path.read_bytes()
            )
            name = metadata.get("Name")
            if name:
                result[canonical_name(name)] = (metadata, dist_info)
    return result


def metadata_source(metadata: Any) -> str | None:
    preferred = ("repository", "source", "homepage", "code")
    urls: dict[str, str] = {}
    for entry in metadata.get_all("Project-URL", []):
        if "," in entry:
            label, url = entry.split(",", 1)
            urls[label.strip().lower()] = url.strip()
    for label in preferred:
        if label in urls:
            return urls[label]
    return metadata.get("Home-page")


def metadata_license_documents(metadata: Any, dist_info: Path) -> list[tuple[str, str]]:
    explicit: list[Path] = []
    for name in metadata.get_all("License-File", []):
        relative = Path(name)
        explicit.extend(
            (
                dist_info / relative,
                dist_info / "licenses" / relative,
                dist_info / "licenses" / relative.name,
            )
        )

    record_path = dist_info / "RECORD"
    if record_path.is_file():
        site_packages = dist_info.parent.resolve()
        with record_path.open(encoding="utf-8", newline="") as record:
            for row in csv.reader(record):
                if not row:
                    continue
                relative = Path(row[0])
                if not LICENSE_NAME.match(relative.name):
                    continue
                candidate = (dist_info.parent / relative).resolve()
                if candidate.is_relative_to(site_packages):
                    explicit.append(candidate)

    documents = root_license_documents(dist_info, explicit)
    if documents:
        return documents
    raw_license = metadata.get("License") or ""
    if "\n" in raw_license:
        text = raw_license.strip()
        if len(text) >= 40:
            return [("METADATA License field", text)]
    return []


def download_locked_artifact(locked: dict[str, Any]) -> tuple[bytes, str]:
    artifact = locked.get("sdist")
    if not artifact:
        wheels = locked.get("wheels") or []
        artifact = wheels[0] if wheels else None
    if not artifact:
        raise RuntimeError(
            f"no locked source artifact for {locked['name']}@{locked['version']}"
        )
    url = artifact["url"]
    request = urllib.request.Request(
        url, headers={"User-Agent": "meeting-supporter-license-audit/1"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    algorithm, expected = artifact["hash"].split(":", 1)
    actual = hashlib.new(algorithm, data).hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"hash mismatch for {url}: expected {expected}, found {actual}"
        )
    return data, url


def archive_members(data: bytes, url: str) -> list[tuple[str, bytes]]:
    members: list[tuple[str, bytes]] = []
    if url.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".tgz")):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            for member in archive.getmembers():
                if member.isfile() and member.size <= 512 * 1024:
                    extracted = archive.extractfile(member)
                    if extracted:
                        members.append((member.name, extracted.read()))
    elif url.endswith((".zip", ".whl")):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if not info.is_dir() and info.file_size <= 512 * 1024:
                    members.append((info.filename, archive.read(info)))
    else:
        raise RuntimeError(f"unsupported locked artifact format: {url}")
    return members


def archive_license_documents(data: bytes, url: str) -> list[tuple[str, str]]:
    documents: list[tuple[str, str]] = []
    for name, content in archive_members(data, url):
        parts = [part for part in Path(name).parts if part not in ("", ".")]
        relative = parts[1:] if len(parts) > 1 else parts
        if len(relative) > 3 or not relative or not LICENSE_NAME.match(relative[-1]):
            continue
        text = clean_text(content)
        if text:
            documents.append(("/".join(relative), text))
    return sorted(documents)


def archive_python_metadata(data: bytes, url: str) -> Any | None:
    candidates = [
        content
        for name, content in archive_members(data, url)
        if name.endswith(".dist-info/METADATA") or name.endswith("/PKG-INFO")
    ]
    if not candidates:
        return None
    return BytesParser(policy=email.policy.compat32).parsebytes(candidates[0])


def python_packages(policy: dict[str, Any]) -> list[Package]:
    exported = run(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-hashes",
            "--no-emit-project",
            "--no-annotate",
            "--no-header",
        ],
        ROOT / "python",
    )
    requirements: set[tuple[str, str]] = set()
    for line in exported.splitlines():
        match = REQUIREMENT.match(line)
        if match:
            requirements.add((match.group(1), match.group(2)))

    lock = tomllib.loads((ROOT / "python" / "uv.lock").read_text(encoding="utf-8"))
    locked_by_key = {
        (canonical_name(item["name"]), item["version"]): item
        for item in lock["package"]
    }
    installed = python_metadata()
    overrides = policy["python_overrides"]
    packages: list[Package] = []
    for name, version in sorted(requirements, key=lambda item: canonical_name(item[0])):
        key = (canonical_name(name), version)
        locked = locked_by_key.get(key)
        if not locked:
            raise RuntimeError(
                f"uv export package is absent from uv.lock: {name}@{version}"
            )
        installed_entry = installed.get(canonical_name(name))
        installed_metadata, dist_info = (
            installed_entry if installed_entry else (None, None)
        )
        data, artifact_url = download_locked_artifact(locked)
        artifact_metadata = archive_python_metadata(data, artifact_url)
        metadata = artifact_metadata or installed_metadata
        override = overrides.get(f"{canonical_name(name)}@{version}")
        expression = (
            override
            or (metadata.get("License-Expression") if metadata else None)
            or (metadata.get("License") if metadata else None)
            or "UNKNOWN"
        )
        repository = metadata_source(metadata) if metadata else None
        source_artifact = locked.get("sdist") or (locked.get("wheels") or [None])[0]
        if not source_artifact:
            raise RuntimeError(f"no locked source URL for {name}@{version}")
        documents = archive_license_documents(data, artifact_url)
        if not documents and installed_metadata and dist_info:
            documents = metadata_license_documents(installed_metadata, dist_info)
        packages.append(
            Package(
                ecosystem="python",
                name=name,
                version=version,
                license_expression=expression,
                source=source_artifact["url"],
                repository=repository or "",
                attribution=(
                    metadata.get("Author-email")
                    or metadata.get("Author")
                    or metadata.get("Maintainer")
                    or ""
                )
                if metadata
                else "",
                documents=documents,
            )
        )
    return packages


def provisioned_packages(policy: dict[str, Any]) -> list[Package]:
    packages: list[Package] = []
    runtime_source = (ROOT / "src-tauri" / "src" / "paths.rs").read_text(
        encoding="utf-8"
    )
    runtime_uv_version = re.search(
        r'^const UV_VERSION: &str = "([^"]+)";$', runtime_source, re.MULTILINE
    )
    if not runtime_uv_version:
        raise RuntimeError("src-tauri/src/paths.rs does not declare UV_VERSION")
    for artifact in policy["provisioned_artifacts"]:
        if artifact["name"] == "uv" and artifact["version"] != runtime_uv_version.group(
            1
        ):
            raise RuntimeError(
                f"uv policy version {artifact['version']} does not match runtime {runtime_uv_version.group(1)}"
            )
        documents: list[tuple[str, str]] = []
        for license_file in artifact["license_files"]:
            request = urllib.request.Request(
                license_file["url"],
                headers={"User-Agent": "meeting-supporter-license-audit/1"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
            actual = hashlib.sha256(data).hexdigest()
            if actual != license_file["sha256"]:
                raise RuntimeError(
                    f"hash mismatch for {license_file['url']}: "
                    f"expected {license_file['sha256']}, found {actual}"
                )
            text = clean_text(data)
            if not text:
                raise RuntimeError(f"empty license text for {license_file['url']}")
            documents.append((license_file["name"], text))
        packages.append(
            Package(
                ecosystem="provisioned",
                name=artifact["name"],
                version=artifact["version"],
                license_expression=artifact["license"],
                source=artifact["source"],
                documents=documents,
            )
        )
    return packages


class ExpressionParser:
    def __init__(self, expression: str, allowed: set[str], exceptions: set[str]):
        self.tokens = [match.group(1) for match in TOKEN.finditer(expression)]
        compact = re.sub(r"\s+", "", expression)
        if "".join(self.tokens).replace(" ", "") != compact:
            raise ValueError(f"unsupported syntax: {expression}")
        self.index = 0
        self.allowed = allowed
        self.exceptions = exceptions

    def parse(self) -> bool:
        result = self.parse_or()
        if self.index != len(self.tokens):
            raise ValueError(f"unexpected token {self.tokens[self.index]}")
        return result

    def parse_or(self) -> bool:
        result = self.parse_and()
        while self.take("OR"):
            alternative = self.parse_and()
            result = result or alternative
        return result

    def parse_and(self) -> bool:
        result = self.parse_primary()
        while self.take("AND"):
            result = self.parse_primary() and result
        return result

    def parse_primary(self) -> bool:
        if self.take("("):
            result = self.parse_or()
            if not self.take(")"):
                raise ValueError("missing closing parenthesis")
        else:
            if self.index >= len(self.tokens):
                raise ValueError("missing license identifier")
            identifier = self.tokens[self.index]
            self.index += 1
            result = identifier in self.allowed
        if self.take("WITH"):
            if self.index >= len(self.tokens):
                raise ValueError("missing license exception")
            exception = self.tokens[self.index]
            self.index += 1
            result = result and exception in self.exceptions
        return result

    def take(self, token: str) -> bool:
        if self.index < len(self.tokens) and self.tokens[self.index] == token:
            self.index += 1
            return True
        return False


def normalized_expression(expression: str, policy: dict[str, Any]) -> str:
    first_line = expression.strip().splitlines()[0]
    normalized = policy["license_aliases"].get(
        expression.strip(), policy["license_aliases"].get(first_line, first_line)
    )
    slash_aliases = {
        "Apache-2.0/MIT": "Apache-2.0 OR MIT",
        "Apache-2.0 / MIT": "Apache-2.0 OR MIT",
        "BSD-3-Clause/MIT": "BSD-3-Clause OR MIT",
        "MIT/Apache-2.0": "MIT OR Apache-2.0",
        "Unlicense/MIT": "Unlicense OR MIT",
    }
    return slash_aliases.get(normalized, normalized)


def pinned_repository_documents(repository: str, commit: str) -> list[tuple[str, str]]:
    normalized = repository.removesuffix(".git").rstrip("/")
    github = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", normalized)
    gitlab = re.match(r"https?://gitlab\.redox-os\.org/(.+)/([^/#?]+)", normalized)
    if github:
        owner, name = github.groups()
        repository_url = f"https://github.com/{owner}/{name}"
        url = f"{repository_url}/archive/{commit}.tar.gz"
    elif gitlab:
        namespace, name = gitlab.groups()
        repository_url = f"https://gitlab.redox-os.org/{namespace}/{name}"
        url = f"{repository_url}/-/archive/{commit}/{name}-{commit}.tar.gz"
    else:
        raise RuntimeError(f"unsupported pinned license repository: {repository}")
    key = (repository_url, commit)
    if key not in REPOSITORY_LICENSE_CACHE:
        request = urllib.request.Request(
            url, headers={"User-Agent": "meeting-supporter-license-audit/1"}
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"failed to fetch pinned license source {url}: {error}"
            ) from error
        documents = archive_license_documents(data, url)
        REPOSITORY_LICENSE_CACHE[key] = documents
    return REPOSITORY_LICENSE_CACHE[key]


def recover_pinned_repository_documents(
    packages: list[Package], policy: dict[str, Any]
) -> None:
    overrides = policy["source_license_overrides"]
    for package in packages:
        if package.documents:
            continue
        override = overrides.get(package.reference, {})
        repository = override.get("repository") or package.repository
        commit = override.get("commit") or package.source_commit
        if not repository or not commit:
            continue
        package.documents = [
            (f"{label} ({repository}@{commit})", text)
            for label, text in pinned_repository_documents(repository, commit)
        ]


def audit(packages: list[Package], policy: dict[str, Any]) -> list[str]:
    allowed = set(policy["allowed_licenses"])
    exceptions = set(policy["allowed_exceptions"])
    errors: list[str] = []
    for package in packages:
        expression = normalized_expression(package.license_expression, policy)
        try:
            accepted = ExpressionParser(expression, allowed, exceptions).parse()
        except ValueError as error:
            errors.append(
                f"{package.reference}: invalid or unknown license expression {expression!r} ({error})"
            )
            continue
        if not accepted:
            errors.append(f"{package.reference}: license is not allowed: {expression}")
        if not package.documents:
            errors.append(
                f"{package.reference}: locked artifact contains no LICENSE/NOTICE text"
            )
    return errors


def render(packages: list[Package]) -> str:
    lines = [
        "THIRD-PARTY SOFTWARE NOTICES",
        "============================",
        "",
        "Meeting Supporter includes or provisions the third-party software listed below.",
        "Python packages are resolved from python/uv.lock with `uv sync --locked --no-dev`.",
        "Corresponding source is available from each package URL. License and attribution",
        "texts are copied from the locked package artifacts; identical texts are deduplicated.",
        "",
        "This file is generated by scripts/third-party-licenses.py. Do not edit manually.",
        "",
    ]
    ecosystem_order = ["provisioned", "npm", "cargo", "python"]
    ecosystem_order.extend(
        sorted({package.ecosystem for package in packages} - set(ecosystem_order))
    )
    for ecosystem in ecosystem_order:
        selected = sorted(
            (package for package in packages if package.ecosystem == ecosystem),
            key=lambda item: (item.name.lower(), item.version),
        )
        lines.extend(
            (
                f"{ecosystem.upper()} COMPONENTS ({len(selected)})",
                "-" * (len(ecosystem) + 16),
            )
        )
        for package in selected:
            expression = package.license_expression.strip().splitlines()[0]
            lines.append(
                f"- {package.name} {package.version} | {expression} | {package.source}"
            )
        lines.append("")

    grouped: dict[str, dict[str, Any]] = {}
    for package in packages:
        for label, text in package.documents:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            entry = grouped.setdefault(digest, {"text": text, "uses": []})
            entry["uses"].append(f"{package.reference} ({label})")

    lines.extend(("LICENSE AND ATTRIBUTION TEXTS", "=============================", ""))
    for index, (digest, entry) in enumerate(sorted(grouped.items()), start=1):
        lines.extend((f"[{index}] SHA-256 {digest}", "Used by:"))
        lines.extend(f"  - {reference}" for reference in sorted(set(entry["uses"])))
        lines.extend(("", entry["text"], "", "-" * 78, ""))
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail unless the committed notice is current",
    )
    args = parser.parse_args()
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    try:
        packages = (
            npm_packages()
            + cargo_packages()
            + python_packages(policy)
            + provisioned_packages(policy)
        )
        recover_pinned_repository_documents(packages, policy)
        errors = audit(packages, policy)
        if errors:
            print("Third-party license audit failed:", file=sys.stderr)
            for error in errors:
                print(f"- {error}", file=sys.stderr)
            return 1
        generated = render(packages)
    except (
        OSError,
        RuntimeError,
        KeyError,
        ValueError,
        urllib.error.URLError,
    ) as error:
        print(f"Third-party license audit failed: {error}", file=sys.stderr)
        return 1

    generated_bytes = generated.encode("utf-8")
    if args.check:
        current = OUTPUT_PATH.read_bytes() if OUTPUT_PATH.is_file() else b""
        if current != generated_bytes:
            print(
                "THIRD-PARTY-NOTICES.txt is stale; run `npm run licenses:generate`.",
                file=sys.stderr,
            )
            return 1
        print(
            f"License audit passed for {len(packages)} distributable packages; notice is current."
        )
        return 0

    OUTPUT_PATH.write_bytes(generated_bytes)
    print(f"Generated {OUTPUT_PATH.name} for {len(packages)} distributable packages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
