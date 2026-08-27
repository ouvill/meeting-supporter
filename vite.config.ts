import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST;

// https://vite.dev/config/
export default defineConfig(async ({ mode }) => ({
  plugins: [react(), tailwindcss()],
  optimizeDeps: {
    // Generated Tauri and website HTML live under this repository. Restrict
    // dependency crawling to the desktop frontend's only HTML entry.
    entries: ["index.html"],
    // The WDIO plugin is only loaded by the dedicated `wdio` build.
    exclude: mode === "wdio" ? [] : ["@wdio/tauri-plugin"],
  },

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // Only the desktop frontend participates in HMR. Avoid recursively
      // traversing backend, Rust, generated, log, and documentation trees.
      ignored: [
        "**/src-tauri/**",
        "**/python/**",
        "**/python-server/**",
        "**/generated/**",
        "**/logs/**",
        "**/dist*/**",
        "**/website/**",
        "**/doc/**",
        "**/test/**",
        "**/scripts/**",
        "**/licenses/**",
      ],
    },
  },
}));
