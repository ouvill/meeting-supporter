import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "openapi.json",
  output: {
    path: "src/api/generated",
    format: "prettier",
  },
  plugins: [
    {
      name: "@hey-api/client-fetch",
      baseUrl: false,
    },
    "@hey-api/sdk",
    "@hey-api/typescript",
  ],
});
