import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
// Keep the WebDriver executable separate from a concurrently running tauri dev.
const env = {
  ...process.env,
  CARGO_TARGET_DIR: join(root, "target/rust-e2e"),
  CARGO_PROFILE_DEV_DEBUG: "0",
  MANAGED_API_BASE_URL: "",
};
async function run(command, args) {
  const child = spawn(command, args, { cwd: root, env, stdio: "inherit" });
  const code = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code) => resolve(code));
  });
  if (code !== 0) throw new Error("Rust desktop E2E build failed");
}
await run("cargo", [
  "build",
  "--locked",
  "--manifest-path",
  "crates/meeting-desktop-runtime/Cargo.toml",
  "--features",
  "test-fixtures",
  "--bin",
  "synthetic-worker",
]);
await run(process.execPath, [
  "node_modules/@tauri-apps/cli/tauri.js",
  "build",
  "--ci",
  "--debug",
  "--no-bundle",
  "--features",
  "rust-backend,webdriver",
  "--config",
  "src-tauri/tauri.rust-wdio.conf.json",
]);
