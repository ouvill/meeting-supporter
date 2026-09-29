"""Prepare pinned developer/test artifacts; not an end-user installer (Python 3.12+)."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def matches(path: Path, digest: str) -> bool:
    if not path.is_file():
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == digest


def download(url: str, destination: Path, digest: str) -> None:
    if matches(destination, digest):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            with urllib.request.urlopen(url, timeout=120) as response:
                shutil.copyfileobj(response, stream)
        if not matches(temporary, digest):
            raise RuntimeError(f"Checksum mismatch: {destination.name}")
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-only", action="store_true")
    parser.add_argument("--with-punctuation", action="store_true")
    args = parser.parse_args()
    if not args.model_only and (
        platform.system() != "Linux" or platform.machine() != "x86_64"
    ):
        parser.error(
            "Native artifacts are verified on Linux x86_64 only; use --model-only"
        )
    # This checked-in manifest is trusted build configuration, never a remote manifest.
    manifest = json.loads((HERE / "assets.json").read_text(encoding="utf-8"))
    model = manifest["model"]
    for name, digest in model["files"].items():
        url = f"https://huggingface.co/{model['repository']}/resolve/{model['revision']}/{name}"
        download(url, HERE / "target/models/reazonspeech" / name, digest)
    if args.with_punctuation:
        subprocess.run(
            [sys.executable, str(HERE / "export_punctuation.py")], check=True
        )
    if not args.model_only:
        runtime = manifest["linux_x86_64_runtime"]
        root = HERE / "target/assets"
        archive = root / "sherpa-onnx.tar.bz2"
        download(runtime["url"], archive, runtime["sha256"])
        directory = root / runtime["directory"]
        if not directory.exists():
            with tempfile.TemporaryDirectory(dir=root) as staging:
                with tarfile.open(archive) as package:
                    package.extractall(staging, filter="data")
                Path(staging, runtime["directory"]).replace(directory)
        if not all(
            matches(directory / "lib" / name, digest)
            for name, digest in runtime["files"].items()
        ):
            raise RuntimeError(
                "Extracted runtime was modified; remove target/assets and prepare again"
            )
    print("Pinned model and requested native artifacts verified under target/.")


if __name__ == "__main__":
    main()
