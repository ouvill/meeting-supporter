import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { download, root, suffix } from "./native-runtime.mjs";

const temporary = await mkdtemp(join(tmpdir(), "meeting bundle 日本語 "));
const env = { PATH: "", TEMP: temporary, TMP: temporary, TMPDIR: temporary };
for (const key of ["SYSTEMROOT", "WINDIR"])
  if (process.env[key]) env[key] = process.env[key];
try {
  // A resource directory extracted from an installer can also be supplied.
  const native = join(temporary, "native");
  await cp(
    resolve(
      process.env.MEETING_TEST_NATIVE_BUNDLE ??
        join(root, "generated/rust-resources/native"),
    ),
    native,
    { recursive: true },
  );
  const executable = join(native, "meeting-native-backend" + suffix);
  function worker(args, input) {
    const result = spawnSync(executable, args, {
      cwd: temporary,
      env,
      encoding: "utf8",
      timeout: 180_000,
      maxBuffer: 1024 * 1024,
      windowsHide: true,
      input,
    });
    assert.ifError(result.error);
    // Output contains only synthetic input and local model diagnostics.
    assert.equal(
      result.status,
      0,
      `Packaged speech worker failed (${result.status}): ${result.stderr}`,
    );
    return result.stdout;
  }
  assert.deepEqual(JSON.parse(worker(["--capabilities"])), {
    protocol: 1,
    whisper_gpu: false,
  });
  // Load the adjacent ONNX Runtime and embedded Silero, and process real PCM.
  const wav = Buffer.alloc(44 + 32000);
  wav.write("RIFF");
  wav.writeUInt32LE(wav.length - 8, 4);
  wav.write("WAVEfmt ", 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(16000, 24);
  wav.writeUInt32LE(32000, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36);
  wav.writeUInt32LE(32000, 40);
  const input = join(temporary, "合成 silence.wav");
  await writeFile(input, wav);
  const requests =
    [
      { op: "prepare" },
      { op: "audio", role: "self", pcm: Array(480).fill(0) },
      { op: "finish", role: "self" },
      { op: "shutdown" },
    ]
      .map((command, id) => JSON.stringify({ id, command }))
      .join("\n") + "\n";
  const replies = worker([], requests)
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  assert.deepEqual(
    replies.map((reply) => reply.type),
    ["ready", "prepared", "audio", "finished", "stopped"],
  );
  if (process.argv.includes("--models")) {
    const { model, whisper_smoke: whisper } = JSON.parse(
      await readFile(
        join(root, "test/rust-native-backend/assets.json"),
        "utf8",
      ),
    );
    const models = join(root, "target/bundle-smoke-models");
    for (const [name, digest] of Object.entries(model.files)) {
      await download(
        `https://huggingface.co/${model.repository}/resolve/${model.revision}/${name}`,
        join(models, "reazon", name),
        digest,
      );
    }
    worker(["--reazon-model", join(models, "reazon"), "--wav", input]);
    const whisperModel = join(models, whisper.file);
    await download(
      `https://huggingface.co/${whisper.repository}/resolve/${whisper.revision}/${whisper.file}`,
      whisperModel,
      whisper.sha256,
    );
    worker([
      "--whisper-model",
      whisperModel,
      "--inference-device",
      "cpu",
      "--wav",
      input,
    ]);
  }
  console.log(
    "Relocated CPU speech worker passed with an empty PATH (Silero" +
      (process.argv.includes("--models") ? " + ReazonSpeech + Whisper" : "") +
      ").",
  );
} finally {
  await rm(temporary, { recursive: true, force: true });
}
