import { spawn } from "node:child_process";
import {
  access,
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
if (
  !["linux", "win32"].includes(process.platform) ||
  (process.platform === "linux" && !process.env.DISPLAY)
) {
  throw new Error(
    "Rust desktop E2E requires Windows or Linux with DISPLAY (xvfb-run -a).",
  );
}
const suffix = process.platform === "win32" ? ".exe" : "";
const worker = resolve(
  root,
  "target/rust-e2e",
  "debug/synthetic-worker" + suffix,
);
await access(worker);
const temporary = await mkdtemp(join(tmpdir(), "meeting-rust-e2e-"));
const model = join(temporary, "model");
const traps = join(temporary, "bin");
const environment = {};
// Pass only desktop/tooling essentials; provider credentials and model overrides
// from the developer's shell must not reach the application or its children.
for (const key of [
  "PATH",
  "DISPLAY",
  "XAUTHORITY",
  "XDG_RUNTIME_DIR",
  "DBUS_SESSION_BUS_ADDRESS",
  "LANG",
  "LC_ALL",
  "LD_LIBRARY_PATH",
  "CI",
  "SYSTEMROOT",
  "WINDIR",
  "COMSPEC",
  "PATHEXT",
]) {
  if (process.env[key]) environment[key] = process.env[key];
}
Object.assign(environment, {
  GDK_BACKEND: "x11",
  XDG_SESSION_TYPE: "x11",
  XDG_DATA_HOME: join(temporary, "data"),
  XDG_CONFIG_HOME: join(temporary, "config"),
  XDG_CACHE_HOME: join(temporary, "cache"),
  HF_HOME: join(temporary, "hub"),
  HF_HUB_OFFLINE: "1",
  TEMP: temporary,
  TMP: temporary,
  APPDATA: join(temporary, "appdata"),
  LOCALAPPDATA: join(temporary, "local-appdata"),
  MEETING_E2E_DATA_DIR: join(temporary, "data"),
  MEETING_AUDIO_WORKER: worker,
  MEETING_E2E_APP: join(
    root,
    "target/rust-e2e/debug/meeting-supporter" + suffix,
  ),
  MEETING_REAZON_WORKER: worker,
  MEETING_REAZON_MODEL: model,
  MEETING_REAZON_PUNCTUATION: model,
  MEETING_PYTHON_WORKER: join(traps, "python" + suffix),
  MEETING_E2E_PYTHON_MARKER: join(temporary, "python-invoked"),
  PATH: traps + delimiter + (environment.PATH ?? ""),
});

function exited(child) {
  return new Promise((resolveExit, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolveExit({ code, signal }));
  });
}
let windowManager;
let test;
try {
  await mkdir(model);
  await mkdir(traps);
  for (const name of [
    "tokens.txt",
    "encoder-epoch-99-avg-1.int8.onnx",
    "decoder-epoch-99-avg-1.int8.onnx",
    "joiner-epoch-99-avg-1.int8.onnx",
  ]) {
    await writeFile(join(model, name), "synthetic model marker");
  }
  for (const name of ["python", "python3", "uv"]) {
    if (process.platform === "win32") {
      await copyFile(worker, join(traps, name + suffix));
      continue;
    }
    await writeFile(
      join(traps, name),
      '#!/bin/sh\nprintf invoked > "$MEETING_E2E_PYTHON_MARKER"\nexit 97\n',
      { mode: 0o700 },
    );
  }
  if (process.platform === "linux") {
    // Each invocation runs on a private Xvfb display in CI.
    windowManager = spawn("openbox", [], { env: environment, stdio: "ignore" });
    windowManager.on("error", () => {});
    let ready = false;
    for (let attempt = 0; attempt < 30; attempt++) {
      if (windowManager.exitCode !== null) break;
      const probe = spawn("openbox", ["--reconfigure"], {
        env: environment,
        stdio: "ignore",
      });
      if ((await exited(probe)).code === 0) {
        ready = true;
        break;
      }
      await new Promise((done) => setTimeout(done, 100));
    }
    if (!ready) throw new Error("openbox did not become ready");
  }
  test = spawn(
    process.execPath,
    [
      "scripts/run-tauri-wdio.mjs",
      "test/tauri/rust.wdio.conf.ts",
      ...process.argv.slice(2),
    ],
    {
      cwd: root,
      env: environment,
      stdio: "inherit",
    },
  );
  const result = await exited(test);
  const invoked = await readFile(
    environment.MEETING_E2E_PYTHON_MARKER,
    "utf8",
  ).catch((error) => {
    if (error.code === "ENOENT") return "";
    throw error;
  });
  if (invoked) throw new Error("Rust E2E unexpectedly invoked Python or uv");
  if (result.signal) throw new Error(`Rust E2E terminated by ${result.signal}`);
  process.exitCode = result.code ?? 1;
} finally {
  if (test && test.exitCode === null && test.signalCode === null)
    test.kill("SIGTERM");
  if (
    windowManager &&
    windowManager.exitCode === null &&
    windowManager.signalCode === null
  ) {
    const stopped = exited(windowManager);
    windowManager.kill("SIGTERM");
    await Promise.race([
      stopped,
      new Promise((done) => setTimeout(done, 2_000)),
    ]);
    if (windowManager.exitCode === null && windowManager.signalCode === null) {
      windowManager.kill("SIGKILL");
      await stopped;
    }
  }
  await rm(temporary, { recursive: true, force: true });
}
