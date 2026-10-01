import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import {
  mkdir,
  readFile,
  rename,
  rm,
  mkdtemp,
  writeFile,
} from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const root = dirname(dirname(fileURLToPath(import.meta.url)));
export const suffix = process.platform === "win32" ? ".exe" : "";

export function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: root,
      stdio: "inherit",
      windowsHide: true,
      ...options,
    });
    child.once("error", reject);
    child.once("exit", (code, signal) =>
      code === 0
        ? resolve()
        : reject(new Error(`${basename(command)} failed (${signal ?? code})`)),
    );
  });
}

export async function matches(path, digest) {
  try {
    return (
      createHash("sha256")
        .update(await readFile(path))
        .digest("hex") === digest
    );
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

export async function download(url, destination, digest) {
  if (await matches(destination, digest)) return;
  await mkdir(dirname(destination), { recursive: true });
  const temporary = `${destination}.download`;
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(180_000) });
    if (!response.ok)
      throw new Error(`Artifact download failed (${response.status})`);
    await writeFile(temporary, Buffer.from(await response.arrayBuffer()));
    if (!(await matches(temporary, digest)))
      throw new Error("Artifact SHA-256 mismatch");
    await rename(temporary, destination);
  } finally {
    await rm(temporary, { force: true });
  }
}

export async function prepareRuntime() {
  if (
    process.arch !== "x64" ||
    !["linux", "win32"].includes(process.platform)
  ) {
    throw new Error(
      "Rust installers currently support Linux x64 and Windows x64 only.",
    );
  }
  const manifest = JSON.parse(
    await readFile(join(root, "test/rust-native-backend/assets.json"), "utf8"),
  );
  const runtime =
    manifest[
      process.platform === "win32"
        ? "windows_x86_64_runtime"
        : "linux_x86_64_runtime"
    ];
  const cache = join(root, "generated/native-runtime");
  const archive = join(cache, `${runtime.directory}.tar.bz2`);
  console.info("Preparing verified native speech libraries...");
  await download(runtime.url, archive, runtime.sha256);
  console.info("Native runtime archive verified; extracting...");
  // Always unpack the verified archive, so an altered cache cannot reach a build.
  const staging = await mkdtemp(join(cache, "extract-"));
  const directory = join(cache, runtime.directory);
  try {
    if (process.platform === "win32") {
      // Windows tar delegates bzip2 to an external process that hangs on the
      // hosted runner. Reuse the DOCX build's uv/Python toolchain and stdlib.
      await run(
        "uv",
        [
          "run",
          "--no-project",
          "--python",
          "3.12",
          "python",
          "-c",
          "import sys, tarfile\nwith tarfile.open(sys.argv[1], 'r:bz2') as archive:\n    archive.extractall(sys.argv[2], filter='data')",
          archive,
          staging,
        ],
        { timeout: 240_000 },
      );
    } else {
      await run("tar", ["-xjf", archive, "-C", staging], {
        timeout: 120_000,
      });
    }
    for (const [name, digest] of Object.entries(runtime.files)) {
      if (
        !(await matches(join(staging, runtime.directory, "lib", name), digest))
      ) {
        throw new Error(`Native runtime file checksum mismatch: ${name}`);
      }
    }
    await rm(directory, { recursive: true, force: true });
    await rename(join(staging, runtime.directory), directory);
  } finally {
    await rm(staging, { recursive: true, force: true });
  }
  console.info("Native speech libraries verified and ready.");
  return join(directory, "lib");
}
