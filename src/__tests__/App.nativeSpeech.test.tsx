import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import App from "../App";
vi.mock("@tauri-apps/api/core", () => ({
  isTauri: () => true,
  invoke: vi.fn(),
}));
vi.mock("../platform/nativeSpeechClient", () => ({
  runtimeMode: async () => "rust",
}));
vi.mock("../platform/tauriWindow", () => ({
  getCurrentAppWindowLabel: () => "main",
}));
vi.mock("../hooks/useBackendBootstrapStatus", () => ({
  useBackendBootstrapStatus: () => {
    throw new Error("Desktop bootstrap must not mount in Rust mode");
  },
}));
vi.mock("../components/native/NativeSpeechScreen", () => ({
  NativeSpeechScreen: () => <main>ローカル文字起こし</main>,
}));
it("opens the Rust screen without mounting desktop API hooks", async () => {
  render(<App />);
  expect(await screen.findByText("ローカル文字起こし")).toBeInTheDocument();
});
