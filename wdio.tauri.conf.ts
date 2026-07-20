import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Options } from "@wdio/types";

const configDir = path.dirname(fileURLToPath(import.meta.url));
const appBinaryPath = path.resolve(
  configDir,
  "src-tauri",
  "target",
  "debug",
  process.platform === "win32" ? "meeting-supporter.exe" : "meeting-supporter",
);

export const config: Options.Testrunner = {
  runner: "local",
  logLevel: "error",
  specs: [
    [
      "./test/tauri/accessibility.wdio.ts",
      "./test/tauri/settings.wdio.ts",
      "./test/tauri/window-controls.wdio.ts",
      "./test/tauri/reply-controls.wdio.ts",
    ],
  ],
  exclude: ["./test/tauri/live-codex.wdio.ts"],
  maxInstances: 1,
  capabilities: [
    {
      browserName: "tauri",
      "tauri:options": {
        application: appBinaryPath,
      },
    },
  ],
  services: [
    [
      "@wdio/tauri-service",
      {
        driverProvider: "embedded",
        appBinaryPath,
        windowLabel: "main",
        startTimeout: 120000,
        commandTimeout: 30000,
        captureBackendLogs: true,
        captureFrontendLogs: false,
      },
    ],
  ],
  framework: "mocha",
  reporters: ["spec"],
  waitforTimeout: 10000,
  connectionRetryTimeout: 120000,
  connectionRetryCount: 1,
  mochaOpts: {
    timeout: 120000,
  },
};
