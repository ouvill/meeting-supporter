import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const { scripts } = JSON.parse(
  readFileSync(join(root, "package.json"), "utf8"),
);

function fixture(t) {
  const directory = mkdtempSync(join(tmpdir(), "rust workers 日本語 "));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  mkdirSync(join(directory, "scripts"));
  mkdirSync(join(directory, "bin"));
  mkdirSync(join(directory, "test/rust-native-backend"), { recursive: true });
  copyFileSync(
    join(root, "scripts/build-rust-workers.mjs"),
    join(directory, "scripts/build-rust-workers.mjs"),
  );
  copyFileSync(
    join(root, "test/rust-native-backend/assets.json"),
    join(directory, "test/rust-native-backend/assets.json"),
  );
  writeFileSync(join(directory, "package.json"), JSON.stringify({ scripts }));
  // Exercise the real dev command without models, audio, Cargo, or a GUI.
  for (const name of ["cargo", "tauri"]) {
    writeFileSync(
      join(directory, "bin", name),
      `#!${process.execPath}
import { appendFileSync } from "node:fs";
const args = process.argv.slice(2);
appendFileSync("calls.jsonl", JSON.stringify({ name: "${name}", args }) + "\\n");
if ("${name}" === "cargo" && args.includes(process.env.FAIL_WORKER)) process.exit(42);
`,
      { mode: 0o755 },
    );
  }
  writeFileSync(join(directory, "bin/package.json"), '{"type":"module"}');
  const env = {
    PATH: `${join(directory, "bin")}:${process.env.PATH}`,
    HOME: directory,
    npm_config_cache: join(directory, ".npm"),
    npm_config_update_notifier: "false",
    npm_config_ignore_scripts: "true",
  };
  function run(args, extra = {}) {
    const result = spawnSync("npm", ["run", ...args], {
      cwd: directory,
      env: { ...env, ...extra },
      encoding: "utf8",
      timeout: 30000,
    });
    assert.ifError(result.error);
    return result;
  }
  function calls() {
    try {
      return readFileSync(join(directory, "calls.jsonl"), "utf8")
        .trim()
        .split("\n")
        .map((line) => JSON.parse(line));
    } catch (error) {
      if (error.code === "ENOENT") return [];
      throw error;
    }
  }
  return { directory, run, calls };
}

// The fake executables use POSIX shebangs; the production runner uses no shell.
const options = { skip: process.platform === "win32" };

test(
  "dev builds workers even with npm lifecycle hooks disabled",
  options,
  (t) => {
    const { run, calls } = fixture(t);
    for (let index = 0; index < 2; index++) {
      const result = run(["dev:rust"]);
      assert.equal(result.status, 0, result.stderr);
    }
    assert.deepEqual(
      calls().map(({ name }) => name),
      ["cargo", "cargo", "tauri", "cargo", "cargo", "tauri"],
    );
  },
);

test("either worker build failure prevents Tauri startup", options, (t) => {
  const { run, calls } = fixture(t);
  for (const worker of ["meeting-audio-runtime", "meeting-native-backend"]) {
    const result = run(["dev:rust"], { FAIL_WORKER: worker });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /build failed/);
  }
  assert.ok(calls().every(({ name }) => name === "cargo"));
});

test(
  "explicit Vulkan selection survives a failed build and the next startup",
  options,
  (t) => {
    const { run, calls } = fixture(t);
    const selection = run(["build:rust-workers", "--", "--gpu", "vulkan"], {
      FAIL_WORKER: "meeting-native-backend",
    });
    assert.notEqual(selection.status, 0);
    const startup = run(["dev:rust"]);
    assert.equal(startup.status, 0, startup.stderr);
    const speechBuilds = calls().filter(({ args }) =>
      args.includes("meeting-native-backend"),
    );
    assert.equal(speechBuilds.length, 2);
    for (const { args } of speechBuilds) {
      assert.equal(args[args.indexOf("--features") + 1], "reazonspeech,vulkan");
    }
  },
);

test("an existing GPU worker is not silently rebuilt as CPU", options, (t) => {
  const { directory, run, calls } = fixture(t);
  const release = join(directory, "test/rust-native-backend/target/release");
  mkdirSync(release, { recursive: true });
  writeFileSync(
    join(release, "meeting-native-backend"),
    `#!${process.execPath}\nconsole.log(JSON.stringify({protocol:1,whisper_gpu:true}));\n`,
    { mode: 0o755 },
  );
  const result = run(["dev:rust"]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Choose the existing worker's GPU backend/);
  assert.deepEqual(calls(), []);
});

test("invalid saved build options prevent startup", options, (t) => {
  const { directory, run, calls } = fixture(t);
  writeFileSync(
    join(directory, ".rust-workers.local.json"),
    '{"gpu":"unknown"}',
  );
  const result = run(["dev:rust"]);
  assert.notEqual(result.status, 0);
  assert.deepEqual(calls(), []);
});

test(
  "explicit external workers are left untouched and reported",
  options,
  (t) => {
    const { directory, run, calls } = fixture(t);
    const external = join(directory, "external-worker");
    writeFileSync(external, "external fixture");
    const result = run(["dev:rust"], { MEETING_REAZON_WORKER: external });
    assert.equal(result.status, 0, result.stderr);
    assert.match(
      result.stdout,
      /MEETING_REAZON_WORKER.*automatic build skipped/,
    );
    assert.deepEqual(
      calls().map(({ name }) => name),
      ["cargo", "tauri"],
    );
  },
);

// Windows cannot execute the POSIX fixture scripts above. Intercept only Cargo
// in a child Node process so the real runner still handles paths and files.
function windowsFixture(t) {
  const directory = mkdtempSync(join(tmpdir(), "rust workers 日本語 "));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  mkdirSync(join(directory, "scripts"));
  copyFileSync(
    join(root, "scripts/build-rust-workers.mjs"),
    join(directory, "scripts/build-rust-workers.mjs"),
  );
  const preload = join(directory, "cargo-fixture.mjs");
  writeFileSync(
    preload,
    `
import childProcess from "node:child_process";
import { EventEmitter } from "node:events";
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import { join } from "node:path";
childProcess.spawn = (command, args) => {
  if (command !== "cargo") throw new Error("Unexpected child command");
  appendFileSync("calls.jsonl", JSON.stringify(args) + "\\n");
  const worker = args[args.indexOf("--bin") + 1];
  const release = join(args[args.indexOf("--target-dir") + 1], "release");
  mkdirSync(release, { recursive: true });
  const content = process.env.FAIL_WORKER === worker ? "failed build" : "new build";
  for (const name of [worker + ".exe", "onnxruntime.dll", "sherpa.DLL", "worker.pdb"]) {
    writeFileSync(join(release, name), content);
  }
  const child = new EventEmitter();
  process.nextTick(() => child.emit("exit", process.env.FAIL_WORKER === worker ? 42 : 0));
  return child;
};
syncBuiltinESMExports();
`,
  );
  function run(extra = {}) {
    const result = spawnSync(
      process.execPath,
      [
        "--import",
        pathToFileURL(preload).href,
        "scripts/build-rust-workers.mjs",
        "--gpu",
        "cpu",
      ],
      {
        cwd: directory,
        env: {
          SystemRoot: process.env.SystemRoot,
          USERPROFILE: directory,
          TEMP: directory,
          TMP: directory,
          CARGO_TARGET_DIR: join(directory, "unrelated-target"),
          ...extra,
        },
        encoding: "utf8",
        timeout: 30000,
      },
    );
    assert.ifError(result.error);
    return result;
  }
  return { directory, run };
}

const windowsOptions = { skip: process.platform !== "win32" };
const speechFiles = ["meeting-native-backend.exe", "onnxruntime.dll", "sherpa.DLL"];

test("Windows stages the speech executable and DLLs from a shorter build path", windowsOptions, (t) => {
  const { directory, run } = windowsFixture(t);
  const runtime = join(directory, "test/rust-native-backend/target/release");
  const result = run();
  assert.equal(result.status, 0, result.stderr);
  const calls = readFileSync(join(directory, "calls.jsonl"), "utf8")
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  const speech = calls.find((args) => args.includes("meeting-native-backend"));
  const target = speech[speech.indexOf("--target-dir") + 1];
  assert.ok(target.length < dirname(runtime).length);
  for (const name of speechFiles) {
    assert.equal(readFileSync(join(runtime, name), "utf8"), "new build");
  }
  assert.equal(existsSync(join(runtime, "worker.pdb")), false);
  assert.equal(existsSync(join(directory, "unrelated-target")), false);

  // An explicit default-path override still builds and publishes to that path.
  const override = run({
    MEETING_REAZON_WORKER: join(runtime, "meeting-native-backend.exe"),
  });
  assert.equal(override.status, 0, override.stderr);
});

test("a failed Windows speech build preserves the existing executable and DLLs", windowsOptions, (t) => {
  const { directory, run } = windowsFixture(t);
  const first = run();
  assert.equal(first.status, 0, first.stderr);
  const failed = run({ FAIL_WORKER: "meeting-native-backend" });
  assert.notEqual(failed.status, 0);
  assert.match(failed.stderr, /meeting-native-backend build failed/);
  const runtime = join(directory, "test/rust-native-backend/target/release");
  for (const name of speechFiles) {
    assert.equal(readFileSync(join(runtime, name), "utf8"), "new build");
  }
});
