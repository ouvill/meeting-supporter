import path from "node:path";
import { mkdir } from "node:fs/promises";
import { browser } from "@wdio/globals";
import { config as base } from "../../wdio.tauri.conf";

export const config: WebdriverIO.Config = {
  ...base,
  specs: [
    [
      path.resolve("test/tauri/rust-backend.wdio.ts"),
      path.resolve("test/tauri/accessibility.wdio.ts"),
      path.resolve("test/tauri/settings.wdio.ts"),
      path.resolve("test/tauri/window-controls.wdio.ts"),
      path.resolve("test/tauri/reply-controls.wdio.ts"),
    ],
  ],
  afterTest: async (_test, _context, { passed }) => {
    if (!passed) {
      await mkdir("logs", { recursive: true });
      await browser.saveScreenshot(`logs/rust-e2e-failure-${process.pid}.png`);
    }
  },
};
