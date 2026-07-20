import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const delay = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

function waitForExit(child) {
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
}

async function prepareBackendEnvironment(venvPath) {
  await mkdir(dirname(venvPath), { recursive: true });
  const environment = {
    ...process.env,
    UV_PROJECT_ENVIRONMENT: venvPath,
  };
  delete environment.VIRTUAL_ENV;
  const sync = spawn(
    "uv",
    ["sync", "--locked", "--no-dev", "--python", "3.12"],
    {
      cwd: join(repoRoot, "python"),
      env: environment,
      stdio: "inherit",
    },
  );
  const result = await waitForExit(sync);
  if (result.signal) {
    throw new Error(`Backend environment setup terminated by ${result.signal}`);
  }
  if (result.code !== 0) {
    throw new Error(
      `Backend environment setup exited with code ${result.code ?? "unknown"}`,
    );
  }
}

async function waitForWindowManager(windowManager) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (windowManager.exitCode !== null) {
      throw new Error(`openbox exited before readiness (${windowManager.exitCode})`);
    }

    const probe = spawn("openbox", ["--reconfigure"], { stdio: "ignore" });
    const result = await waitForExit(probe);
    if (result.code === 0) return;
    await delay(100);
  }
  throw new Error("openbox did not become ready");
}

const repoRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const wdioConfig = JSON.parse(
  await readFile(join(repoRoot, "src-tauri", "tauri.wdio.conf.json"), "utf-8"),
);
if (typeof wdioConfig.identifier !== "string" || !wdioConfig.identifier) {
  throw new Error("Tauri WDIO config must provide an application identifier");
}

if (process.platform !== "linux") {
  throw new Error("run-linux-tauri-e2e.mjs is Linux-only");
}
if (!process.env.DISPLAY) {
  throw new Error("DISPLAY is required; run this script under xvfb-run");
}

const windowManager = spawn("openbox", [], { stdio: "ignore" });
const testDataHome = await mkdtemp(
  join(tmpdir(), "meeting-supporter-tauri-e2e-"),
);
const backendVenv = join(
  testDataHome,
  "data",
  wdioConfig.identifier,
  ".venv",
);
let testResult;
try {
  await waitForWindowManager(windowManager);
  await prepareBackendEnvironment(backendVenv);
  const test = spawn("npm", ["run", "test:tauri"], {
    stdio: "inherit",
    env: {
      ...process.env,
      GDK_BACKEND: "x11",
      XDG_CACHE_HOME: join(testDataHome, "cache"),
      XDG_DATA_HOME: join(testDataHome, "data"),
      XDG_SESSION_TYPE: "x11",
    },
  });
  testResult = await waitForExit(test);
} finally {
  windowManager.kill("SIGTERM");
  if (windowManager.exitCode === null) {
    await Promise.race([waitForExit(windowManager), delay(2_000)]);
  }
  await rm(testDataHome, { force: true, recursive: true });
}

if (testResult.signal) {
  throw new Error(`Tauri E2E terminated by ${testResult.signal}`);
}
process.exitCode = testResult.code ?? 1;
