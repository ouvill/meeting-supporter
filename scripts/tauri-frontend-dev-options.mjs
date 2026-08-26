export const TAURI_FRONTEND_NODE_ENV = "development";

export const TAURI_FRONTEND_BUILD_OPTIONS = Object.freeze({
  mode: "development",
  clearScreen: false,
  define: {
    "import.meta.env.DEV": "true",
    "import.meta.env.PROD": "false",
  },
  build: {
    minify: false,
    sourcemap: true,
    watch: {},
  },
});

export const TAURI_FRONTEND_PREVIEW_OPTIONS = Object.freeze({
  clearScreen: false,
  preview: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
  },
});
