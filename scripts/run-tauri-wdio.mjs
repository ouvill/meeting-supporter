import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";
import { dirname, resolve } from "node:path";

function waitForExit(child) {
  return new Promise((resolveExit, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolveExit({ code, signal }));
  });
}

function findAvailablePort() {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0, exclusive: true }, () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("Could not allocate an embedded WebDriver port"));
        return;
      }
      const { port } = address;
      server.close((error) => {
        if (error) reject(error);
        else resolvePort(port);
      });
    });
  });
}

const repoRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const configPath = resolve(repoRoot, process.argv[2] ?? "wdio.tauri.conf.ts");
const embeddedPort =
  process.env.TAURI_WEBDRIVER_PORT ?? String(await findAvailablePort());
console.log(`Using embedded WebDriver port ${embeddedPort}`);

const child = spawn(
  process.execPath,
  [
    resolve(repoRoot, "node_modules", "@wdio", "cli", "bin", "wdio.js"),
    "run",
    configPath,
  ],
  {
    cwd: repoRoot,
    env: { ...process.env, TAURI_WEBDRIVER_PORT: embeddedPort },
    stdio: "inherit",
  },
);
const result = await waitForExit(child);
if (result.signal) {
  throw new Error(`Tauri WDIO terminated by ${result.signal}`);
}
process.exitCode = result.code ?? 1;
