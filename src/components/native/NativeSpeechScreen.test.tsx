import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NativeSpeechScreen } from "./NativeSpeechScreen";
import {
  speechDefaults,
  speechSnapshot,
  startSpeech,
  stopSpeech,
  type NativeSnapshot,
} from "../../platform/nativeSpeechClient";

vi.mock("../../platform/nativeSpeechClient", async (original) => ({
  ...(await original<typeof import("../../platform/nativeSpeechClient")>()),
  speechDefaults: vi.fn(),
  speechSnapshot: vi.fn(),
  startSpeech: vi.fn(),
  stopSpeech: vi.fn(),
}));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
const idle: NativeSnapshot = {
  generation: 0,
  revision: 0,
  phase: "idle",
  error: null,
  transcripts: [],
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(speechDefaults).mockResolvedValue({
    model_directory: "/models/reazon",
    punctuation_directory: "/models/punctuation",
  });
  vi.mocked(speechSnapshot).mockResolvedValue(idle);
});
describe("local speech screen", () => {
  it("can stop while models are preparing and preserves final raw text", async () => {
    vi.mocked(startSpeech).mockResolvedValue({
      ...idle,
      generation: 1,
      revision: 1,
      phase: "starting",
    });
    vi.mocked(stopSpeech).mockResolvedValue({
      ...idle,
      generation: 1,
      revision: 2,
      phase: "stopped",
      transcripts: [
        {
          generation: 1,
          sequence: 0,
          text: "確認です。",
          raw_text: "確認です",
          start_sample: 0,
          end_sample: 480,
          punctuation_failed: false,
        },
      ],
    });
    render(<NativeSpeechScreen />);
    const start = await screen.findByRole("button", {
      name: "文字起こしを開始",
    });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);
    expect(
      await screen.findByText("音声モデルを準備しています"),
    ).toBeInTheDocument();
    expect(startSpeech).toHaveBeenCalledWith(
      "/models/reazon",
      "/models/punctuation",
    );
    fireEvent.click(screen.getByRole("button", { name: "停止" }));
    expect(await screen.findByText("確認です。")).toBeInTheDocument();
    expect(screen.getByText("確認です")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "テキストを保存" }),
    ).toBeEnabled();
  });
  it("shows recoverable microphone errors without claiming a recording is active", async () => {
    vi.mocked(speechSnapshot).mockResolvedValue({
      ...idle,
      phase: "failed",
      error: "microphone_unavailable",
    });
    render(<NativeSpeechScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "マイクが見つかりません",
    );
    expect(
      screen.queryByRole("button", { name: "停止" }),
    ).not.toBeInTheDocument();
  });
  it("sends no punctuation model when the feature is switched off", async () => {
    vi.mocked(startSpeech).mockResolvedValue({
      ...idle,
      revision: 1,
      phase: "starting",
    });
    render(<NativeSpeechScreen />);
    const start = await screen.findByRole("button", {
      name: "文字起こしを開始",
    });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(screen.getByRole("checkbox", { name: "句読点を付ける" }));
    fireEvent.click(start);
    await waitFor(() =>
      expect(startSpeech).toHaveBeenCalledWith("/models/reazon", null),
    );
  });
});
