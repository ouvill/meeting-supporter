import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const project = join(root, "python-worker");
const output = join(root, "generated", "python-worker");
mkdirSync(output, { recursive: true });
const result = spawnSync(
  "uv",
  [
    "run",
    "--locked",
    "--project",
    project,
    "--group",
    "build",
    "--no-dev",
    "pyinstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name",
    "meeting-python-worker",
    "--distpath",
    join(output, "dist"),
    "--workpath",
    join(output, "build"),
    "--specpath",
    output,
    "--collect-data",
    "magika",
    "--collect-data",
    "markitdown",
    "--collect-data",
    "mammoth",
    join(project, "entry.py"),
  ],
  { cwd: project, stdio: "inherit" },
);
if (result.error) {
  console.error(
    "Python worker build could not start. Install uv in the build environment.",
  );
}
process.exitCode = result.status ?? 1;

if (result.status === 0) {
  const bundle = join(output, "dist", "meeting-python-worker");
  for (const file of ["LICENSE", "THIRD-PARTY-NOTICES.txt"]) {
    copyFileSync(join(root, file), join(bundle, file));
  }
}
