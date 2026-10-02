import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const selectionPath = join(root, ".rust-workers.local.json");
const gpuOptions = ["cpu", "vulkan", "cuda", "metal"];
const suffix = process.platform === "win32" ? ".exe" : "";
const workers = [
  {
    name: "meeting-audio-runtime",
    directory: "crates/meeting-audio-runtime",
    override: "MEETING_AUDIO_WORKER",
  },
  {
    name: "meeting-native-backend",
    directory: "test/rust-native-backend",
    override: "MEETING_REAZON_WORKER",
  },
];

function executable(worker) {
  return join(root, worker.directory, "target/release", worker.name + suffix);
}

function isExternal(worker) {
  const override = process.env[worker.override];
  if (!override) return false;
  if (!isAbsolute(override) || !existsSync(override)) {
    throw new Error(
      `${worker.override} must point to an existing absolute path.`,
    );
  }
  return resolve(override) !== executable(worker);
}

function gpuSelection() {
  const args = process.argv.slice(2);
  if (
    args.length !== 0 &&
    (args.length !== 2 || args[0] !== "--gpu" || !gpuOptions.includes(args[1]))
  ) {
    throw new Error(
      "Usage: npm run build:rust-workers -- --gpu cpu|vulkan|cuda|metal",
    );
  }
  if (args.length === 2) {
    // Remember the explicit choice even if a missing SDK prevents this build.
    // A subsequent dev:rust must retry that choice, not replace it with CPU.
    writeFileSync(
      selectionPath,
      `${JSON.stringify({ gpu: args[1] }, null, 2)}\n`,
    );
    return args[1];
  }
  if (existsSync(selectionPath)) {
    const selection = JSON.parse(readFileSync(selectionPath, "utf8"));
    if (
      !selection ||
      typeof selection !== "object" ||
      Array.isArray(selection) ||
      Object.keys(selection).length !== 1 ||
      !gpuOptions.includes(selection.gpu)
    ) {
      throw new Error(
        "Invalid .rust-workers.local.json: expected a gpu selection.",
      );
    }
    return selection.gpu;
  }
  const speech = workers[1];
  if (!isExternal(speech) && existsSync(executable(speech))) {
    const result = spawnSync(executable(speech), ["--capabilities"], {
      encoding: "utf8",
      timeout: 5000,
      maxBuffer: 65536,
      windowsHide: true,
    });
    let capabilities;
    try {
      capabilities = JSON.parse(result.stdout);
    } catch {
      // An old or unloadable executable also needs an explicit build choice.
    }
    if (
      result.error ||
      result.status !== 0 ||
      capabilities?.protocol !== 1 ||
      capabilities.whisper_gpu !== false
    ) {
      throw new Error(
        "Choose the existing worker's GPU backend once with " +
          "npm run build:rust-workers -- --gpu cpu|vulkan|cuda|metal. " +
          "The existing worker was not replaced.",
      );
    }
  }
  return "cpu";
}

async function build(worker, gpu) {
  if (isExternal(worker)) {
    console.log(
      `[rust-workers] ${worker.override}: using an external worker; automatic build skipped.`,
    );
    return;
  }
  const env = { ...process.env };
  const args = [
    "build",
    "--release",
    "--locked",
    "--manifest-path",
    `${worker.directory}/Cargo.toml`,
    "--bin",
    worker.name,
    // Match the paths used by desktop_runtime.rs even with CARGO_TARGET_DIR set.
    "--target-dir",
    join(root, worker.directory, "target"),
  ];
  if (worker.name === "meeting-native-backend") {
    const features = gpu === "cpu" ? "reazonspeech" : `reazonspeech,${gpu}`;
    args.push("--features", features);
    console.log(
      `[rust-workers] Building speech worker (${features}, whisper).`,
    );
    if (
      !env.SHERPA_ONNX_LIB_DIR &&
      process.platform === "linux" &&
      process.arch === "x64"
    ) {
      const manifest = JSON.parse(
        readFileSync(join(root, worker.directory, "assets.json"), "utf8"),
      );
      const libraries = join(
        root,
        worker.directory,
        "target/assets",
        manifest.linux_x86_64_runtime.directory,
        "lib",
      );
      if (existsSync(libraries)) env.SHERPA_ONNX_LIB_DIR = libraries;
    }
  } else {
    console.log("[rust-workers] Building audio capture worker.");
  }
  const child = spawn("cargo", args, { cwd: root, env, stdio: "inherit" });
  const code = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code) => resolve(code));
  });
  if (code !== 0) throw new Error(`${worker.name} build failed.`);
}

try {
  const gpu = gpuSelection();
  // Validate both overrides before modifying either local executable.
  for (const worker of workers) isExternal(worker);
  for (const worker of workers) await build(worker, gpu);
} catch (error) {
  console.error(`[rust-workers] ${error.message}`);
  process.exitCode = 1;
}
