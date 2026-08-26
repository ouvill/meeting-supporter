// @vitest-environment node

import { describe, expect, it } from "vitest";
import packageJson from "../package.json";
import {
  TAURI_FRONTEND_BUILD_OPTIONS,
  TAURI_FRONTEND_NODE_ENV,
  TAURI_FRONTEND_PREVIEW_OPTIONS,
} from "../scripts/tauri-frontend-dev-options.mjs";
import tauriConfig from "../src-tauri/tauri.conf.json";

describe("Tauri development frontend", () => {
  it("uses a debuggable development bundle on one IPv4 loopback origin", () => {
    expect(TAURI_FRONTEND_NODE_ENV).toBe("development");
    expect(TAURI_FRONTEND_BUILD_OPTIONS.mode).toBe("development");
    expect(TAURI_FRONTEND_BUILD_OPTIONS.define).toEqual({
      "import.meta.env.DEV": "true",
      "import.meta.env.PROD": "false",
    });
    expect(TAURI_FRONTEND_BUILD_OPTIONS.build).toMatchObject({
      minify: false,
      sourcemap: true,
      watch: {},
    });
    expect(TAURI_FRONTEND_PREVIEW_OPTIONS.preview).toEqual({
      host: "127.0.0.1",
      port: 1420,
      strictPort: true,
    });
    expect(tauriConfig.build.devUrl).toBe("http://127.0.0.1:1420");
    expect(tauriConfig.build.beforeDevCommand).toBe(
      "npm run prepare:python-resource && npm run dev:tauri-frontend",
    );
    expect(packageJson.scripts["dev:tauri-frontend"]).toBe(
      "node scripts/run-tauri-frontend-dev.mjs",
    );
  });
});
