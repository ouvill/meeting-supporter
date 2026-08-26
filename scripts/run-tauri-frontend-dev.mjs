import process from "node:process";
import {
  TAURI_FRONTEND_BUILD_OPTIONS,
  TAURI_FRONTEND_NODE_ENV,
  TAURI_FRONTEND_PREVIEW_OPTIONS,
} from "./tauri-frontend-dev-options.mjs";

process.env.NODE_ENV = TAURI_FRONTEND_NODE_ENV;

const { build, preview } = await import("vite");

const result = await build(TAURI_FRONTEND_BUILD_OPTIONS);

if (!result || Array.isArray(result) || typeof result.on !== "function") {
  throw new Error("Vite did not create a development build watcher");
}

const watcher = result;
let previewServer;
let previewStarting = false;
let shuttingDown = false;

watcher.on("event", async (event) => {
  if (event.code === "ERROR") {
    console.error("Tauri frontend build failed", event.error);
    return;
  }
  if (event.code === "BUNDLE_END") {
    await event.result.close();
    return;
  }
  if (event.code !== "END") return;

  if (previewServer) {
    console.log(
      "Tauri frontend rebuilt. Reload the desktop window to apply changes.",
    );
    return;
  }
  if (previewStarting) return;
  previewStarting = true;
  try {
    previewServer = await preview(TAURI_FRONTEND_PREVIEW_OPTIONS);
    previewServer.printUrls();
  } catch (error) {
    console.error("Tauri frontend preview failed", error);
    await watcher.close();
    process.exitCode = 1;
  }
});

async function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  await watcher.close();
  if (previewServer) {
    await new Promise((resolve, reject) => {
      previewServer.httpServer.close((error) => {
        if (error) reject(error);
        else resolve();
      });
    });
  }
}

process.on("SIGINT", () => {
  void shutdown().finally(() => process.exit(130));
});
process.on("SIGTERM", () => {
  void shutdown().finally(() => process.exit(143));
});
