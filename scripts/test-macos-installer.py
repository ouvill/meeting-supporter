"""Install the CI DMG in a relocated directory and exercise its real resources."""

import os
import platform
import plistlib
import subprocess
import tempfile
from pathlib import Path


def run(
    *args: str, env: dict[str, str] | None = None, input: str | None = None
) -> None:
    subprocess.run(args, check=True, env=env, timeout=600, input=input, text=True)


root = Path(__file__).resolve().parent.parent
images = list((root / "src-tauri/target/release/bundle/dmg").glob("*.dmg"))
assert len(images) == 1, "Expected one native DMG"
with tempfile.TemporaryDirectory(prefix="meeting macOS 日本語 ") as temporary:
    directory = Path(temporary)
    mount = directory / "mounted"
    run(
        "hdiutil",
        "attach",
        str(images[0]),
        "-nobrowse",
        "-readonly",
        "-mountpoint",
        str(mount),
        # Tauri embeds the project's AGPL license in the DMG. hdiutil needs an
        # explicit response even in the unattended installer smoke test.
        input="Y\n",
    )
    try:
        apps = list(mount.glob("*.app"))
        assert len(apps) == 1, "DMG application is missing"
        installed = directory / apps[0].name
        run("ditto", str(apps[0]), str(installed))
    finally:
        run("hdiutil", "detach", str(mount))

    contents = installed / "Contents"
    resources = contents / "Resources"
    assert not (resources / "python").exists(), "Legacy backend was bundled"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["LSMinimumSystemVersion"] == "14.6"
    assert info["NSMicrophoneUsageDescription"]
    assert info["NSAudioCaptureUsageDescription"]
    for executable in [
        contents / "MacOS" / info["CFBundleExecutable"],
        resources / "native/meeting-audio-runtime",
        resources / "native/meeting-native-backend",
        resources / "python-worker/meeting-python-worker",
    ]:
        run("lipo", "-verify_arch", platform.machine(), str(executable))
    run("codesign", "--verify", "--deep", "--strict", str(installed))
    run("swift", str(root / "test/macos-app-window.swift"), str(installed))
    env = dict(os.environ)
    env["MEETING_TEST_NATIVE_BUNDLE"] = str(resources / "native")
    env["MEETING_TEST_PYTHON_WORKER"] = str(
        resources / "python-worker/meeting-python-worker"
    )
    run("npm", "run", "test:rust-bundle", "--", "--models", env=env)
    run("npm", "run", "test:python-worker", env=env)
print("Installed macOS application, native workers, and MarkItDown passed.")
