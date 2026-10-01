import { access, copyFile, mkdir, readdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { prepareRuntime, root, run, suffix } from "./native-runtime.mjs";

// Packaging deliberately uses its own CPU build, independent of developer GPU
// selections, external workers, CARGO_TARGET_DIR, and cached development output.
const library = await prepareRuntime();
const development = process.argv.includes("--dev");
const target =
  process.platform === "win32"
    ? "x86_64-pc-windows-msvc"
    : "x86_64-unknown-linux-gnu";
const env = development
  ? { ...process.env, SHERPA_ONNX_LIB_DIR: library }
  : {
      ...process.env,
      SHERPA_ONNX_LIB_DIR: library,
      GGML_NATIVE: "OFF",
      GGML_AVX: "OFF",
      GGML_AVX2: "OFF",
      GGML_FMA: "OFF",
      GGML_F16C: "OFF",
    };
if (!development) {
  delete env.CARGO_ENCODED_RUSTFLAGS;
  env.RUSTFLAGS =
    process.platform === "win32" ? "-C target-feature=+crt-static" : "";
}
const output = join(root, "generated/rust-resources/native");
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
if (development)
  await run(process.execPath, ["scripts/build-rust-workers.mjs"], { env });
for (const [name, directory, features] of [
  ["meeting-audio-runtime", "crates/meeting-audio-runtime", []],
  [
    "meeting-native-backend",
    "test/rust-native-backend",
    ["--features", "reazonspeech"],
  ],
]) {
  // Leave room for CMake/MSBuild's nested scratch paths on Windows.
  const build = join(
    root,
    "target/bundle",
    name === "meeting-audio-runtime" ? "audio" : "speech",
  );
  if (!development)
    await run(
      "cargo",
      [
        "build",
        "--release",
        "--locked",
        "--manifest-path",
        `${directory}/Cargo.toml`,
        "--bin",
        name,
        "--target",
        target,
        "--target-dir",
        build,
        ...features,
      ],
      { env },
    );
  const override =
    name === "meeting-audio-runtime"
      ? "MEETING_AUDIO_WORKER"
      : "MEETING_REAZON_WORKER";
  const binary = development
    ? (process.env[override] ??
      join(root, directory, "target/release", name + suffix))
    : join(build, target, "release", name + suffix);
  await copyFile(binary, join(output, name + suffix));
}
// Include SONAME aliases on Linux and every DLL in the pinned Windows runtime.
// Dereference aliases when copying so installer formats need no symlink support.
for (const name of await readdir(library)) {
  if (/\.dll$|\.so(?:\.|$)/.test(name))
    await copyFile(join(library, name), join(output, name));
}
await copyFile(
  join(root, "THIRD-PARTY-NOTICES.txt"),
  join(output, "THIRD-PARTY-NOTICES.txt"),
);
const pythonWorker = join(
  root,
  "generated/python-worker/dist/meeting-python-worker",
  "meeting-python-worker" + suffix,
);
if (
  !development ||
  !(await access(pythonWorker).then(
    () => true,
    () => false,
  ))
) {
  await run(process.execPath, ["scripts/build-python-worker.mjs"]);
}
if (!development) await run(process.execPath, ["scripts/test-rust-bundle.mjs"]);
