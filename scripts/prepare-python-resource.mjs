import { createHash } from "node:crypto";
import {
  cp,
  lstat,
  mkdir,
  readdir,
  readFile,
  readlink,
  rm,
} from "node:fs/promises";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const sourceRoot = join(repoRoot, "python");
const targetRoot = join(repoRoot, "generated", "tauri", "python");

const entries = [
  "app",
  "main.py",
  "pyproject.toml",
  "uv.lock",
  "config.default.toml",
].sort();
const excludedParts = new Set([
  ".venv",
  "__pycache__",
  ".pytest_cache",
  ".ruff_cache",
]);

function shouldInclude(path) {
  const parts = path.split(/[\\/]+/);
  return !parts.some(
    (part) =>
      excludedParts.has(part) || part.endsWith(".pyc") || part.endsWith(".pyo"),
  );
}

function normalizedPath(path) {
  return path.split(sep).join("/");
}

async function addSnapshotEntry(hash, root, path, filter) {
  if (filter && !shouldInclude(path)) return;

  const absolutePath = join(root, path);
  const stats = await lstat(absolutePath);
  const snapshotPath = normalizedPath(path);

  if (stats.isDirectory()) {
    hash.update(`directory\\0${snapshotPath}\\0`);
    const children = (await readdir(absolutePath)).sort();
    for (const child of children) {
      await addSnapshotEntry(hash, root, join(path, child), filter);
    }
    return;
  }

  if (stats.isFile()) {
    hash.update(`file\\0${snapshotPath}\\0`);
    hash.update(await readFile(absolutePath));
    hash.update("\\0");
    return;
  }

  if (stats.isSymbolicLink()) {
    hash.update(
      `symlink\\0${snapshotPath}\\0${await readlink(absolutePath)}\\0`,
    );
    return;
  }

  throw new Error(`Unsupported resource entry: ${absolutePath}`);
}

async function sourceDigest() {
  const hash = createHash("sha256");
  for (const entry of entries) {
    await addSnapshotEntry(hash, sourceRoot, entry, true);
  }
  return hash.digest("hex");
}

async function targetDigest() {
  const hash = createHash("sha256");
  try {
    const targetEntries = (await readdir(targetRoot)).sort();
    for (const entry of targetEntries) {
      await addSnapshotEntry(hash, targetRoot, entry, false);
    }
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
  return hash.digest("hex");
}

const expectedDigest = await sourceDigest();
if ((await targetDigest()) === expectedDigest) {
  console.log(
    `Sanitized Python resource is already up to date at ${targetRoot}`,
  );
  process.exit(0);
}

await rm(targetRoot, { recursive: true, force: true });
await mkdir(targetRoot, { recursive: true });

for (const entry of entries) {
  await cp(join(sourceRoot, entry), join(targetRoot, entry), {
    recursive: true,
    filter: (source) => shouldInclude(relative(sourceRoot, source)),
  });
}

console.log(`Prepared sanitized Python resource at ${targetRoot}`);
