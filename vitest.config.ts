import { defineConfig, defaultExclude } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    exclude: [
      ...defaultExclude,
      "**/.agents/**",
      "experiments/deno-desktop-spike/tests/**",
      "cloud/managed-service/test/**",
      "scripts/**/*.test.mjs",
    ],
  },
});
