import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { download } from "./native-runtime.mjs";

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
