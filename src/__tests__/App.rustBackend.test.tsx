import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import App from "../App";

vi.mock("@tauri-apps/api/core", () => ({
  isTauri: () => true,
  invoke: vi.fn(),
}));
vi.mock("../platform/nativeSpeechClient", () => ({
  runtimeMode: async () => "rust-backend",
}));
vi.mock("../platform/tauriWindow", () => ({
  getCurrentAppWindowLabel: () => "main",
  setAssistantWindowVisible: async () => {},
}));
vi.mock("../hooks/useBackendBootstrapStatus", () => ({
  useBackendBootstrapStatus: () => ({
    apiPort: null,
    apiAuthToken: null,
    bootstrap: { phase: "starting", message: "Starting Rust backend..." },
    crashInfo: null,
  }),
}));
vi.mock("../hooks/useMeetingSocket", () => ({
  useMeetingSocket: () => ({ send: vi.fn() }),
}));
vi.mock("../components/native/NativeSpeechScreen", () => ({
  NativeSpeechScreen: () => {
    throw new Error("The speech test harness must not replace the normal UI");
  },
}));
it("keeps the existing application shell in Rust backend mode", async () => {
  render(<App />);
  expect(
    await screen.findByText(
      "会議の準備を整えています。このまま少しお待ちください。",
    ),
  ).toBeInTheDocument();
  expect(screen.getByRole("main")).toBeInTheDocument();
});
