import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const uv = process.platform === "win32" ? "uv.exe" : "uv";
const child = spawn(
  uv,
  ["run", "uvicorn", "main:app", "--port", "8000", "--reload"],
  {
    cwd: join(repoRoot, "python"),
    env: { ...process.env, DEBUG: "1" },
    stdio: "inherit",
  },
);

child.on("error", (error) => {
  console.error(`Failed to start Python development server: ${error.message}`);
  process.exitCode = 1;
});

child.on("exit", (code) => {
  process.exitCode = code ?? 1;
});
