import path from "node:path";
import { mkdirSync } from "node:fs";
import { config as base } from "../../wdio.tauri.conf";

// No Python environment or real audio is used. Existing model files are untouched.
process.env.MEETING_NATIVE_WORKER = path.resolve(
  "test/tauri/fixtures/native-speech-worker.sh",
);
mkdirSync("test/rust-native-backend/target/models/reazonspeech", {
  recursive: true,
});
export const config = {
  ...base,
  specs: [path.resolve("test/tauri/native-speech.wdio.ts")],
  exclude: [],
};
