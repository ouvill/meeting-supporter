import type { Options } from "@wdio/types";
import { config as baseConfig } from "./wdio.tauri.conf";

export const config: Options.Testrunner = {
  ...baseConfig,
  specs: ["./test/tauri/live-codex.wdio.ts"],
  exclude: [],
};
