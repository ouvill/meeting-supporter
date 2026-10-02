import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { download, nativeTarget, root } from "./native-runtime.mjs";

test("each supported host selects matching pinned native libraries and compiler target", async () => {
  const manifest = JSON.parse(
    await readFile(join(root, "test/rust-native-backend/assets.json"), "utf8"),
  );
  for (const [platform, arch, triple] of [
    ["linux", "x64", "x86_64-unknown-linux-gnu"],
    ["win32", "x64", "x86_64-pc-windows-msvc"],
    ["darwin", "arm64", "aarch64-apple-darwin"],
    ["darwin", "x64", "x86_64-apple-darwin"],
  ]) {
    const target = nativeTarget(platform, arch);
    assert.equal(target.triple, triple);
    assert.match(manifest[target.runtime].sha256, /^[a-f0-9]{64}$/);
    assert.ok(Object.keys(manifest[target.runtime].files).length >= 3);
  }
  assert.throws(() => nativeTarget("win32", "arm64"), /Unsupported/);
});

test("verified artifact downloads are reused and failed replacements leave no partial file", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "native artifact 日本語 "));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const path = join(directory, "artifact");
  const body = "synthetic native artifact";
  const digest = createHash("sha256").update(body).digest("hex");
  await download(
    `data:application/octet-stream,${encodeURIComponent(body)}`,
    path,
    digest,
  );
  assert.equal(await readFile(path, "utf8"), body);
  // A valid cache is usable offline.
  await download("invalid:offline", path, digest);
  await writeFile(path, "previous version");
  await assert.rejects(
    download("data:application/octet-stream,altered", path, digest),
    /SHA-256 mismatch/,
  );
  assert.equal(await readFile(path, "utf8"), "previous version");
  assert.deepEqual(await readdir(directory), ["artifact"]);
});
