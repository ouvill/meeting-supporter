import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSavedSpeechReadiness } from "./useSavedSpeechReadiness";

const mocks = vi.hoisted(() => ({ settings: vi.fn(), speech: vi.fn() }));
vi.mock("../api/generated/sdk.gen", () => ({
  getSettingsApiSettingsGet: mocks.settings,
}));
vi.mock("./useSpeechModel", async (original) => ({
  ...(await original<typeof import("./useSpeechModel")>()),
  useSpeechModel: mocks.speech,
}));

const configuration = (model = "small", language = "ja") => ({
  data: {
    stt: { backend: "whisper", whisper_model: model, language },
    secrets: {},
  },
});

describe("saved speech readiness", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.settings.mockResolvedValue(configuration());
    mocks.speech.mockReturnValue({
      status: { state: "ready" },
      loading: false,
      error: null,
    });
  });

  it("checks the saved model before allowing the first start", async () => {
    const { result } = renderHook(() => useSavedSpeechReadiness(false, true));
    expect(result.current).toBe("checking");
    await waitFor(() => expect(result.current).toBe("ready"));
    expect(mocks.speech).toHaveBeenLastCalledWith(
      "whisper",
      "small",
      "ja",
      true,
    );
  });

  it("rechecks configuration after settings close instead of using the previous model", async () => {
    const view = renderHook(({ open }) => useSavedSpeechReadiness(open, true), {
      initialProps: { open: false },
    });
    await waitFor(() => expect(view.result.current).toBe("ready"));
    view.rerender({ open: true });
    mocks.settings.mockResolvedValue(configuration("medium", "auto"));
    mocks.speech.mockReturnValue({
      status: { state: "missing" },
      loading: false,
      error: null,
    });
    view.rerender({ open: false });
    await waitFor(() => expect(view.result.current).toBe("missing"));
    expect(mocks.speech).toHaveBeenLastCalledWith(
      "whisper",
      "medium",
      "ja",
      true,
    );
  });

  it("does not treat unavailable configuration as ready", async () => {
    mocks.settings.mockRejectedValue(new Error("unavailable"));
    const { result } = renderHook(() => useSavedSpeechReadiness(false, true));
    await waitFor(() => expect(result.current).toBe("error"));
  });

  it("does not request an unknown model identifier", async () => {
    mocks.settings.mockResolvedValue(configuration("unsupported"));
    const { result } = renderHook(() => useSavedSpeechReadiness(false, true));
    await waitFor(() => expect(result.current).toBe("error"));
    expect(mocks.speech).toHaveBeenLastCalledWith("whisper", null, "ja", false);
  });

  it("ignores a stale settings response after disconnect", async () => {
    let resolve: (value: ReturnType<typeof configuration>) => void = () => {};
    mocks.settings.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const view = renderHook(
      ({ connected }) => useSavedSpeechReadiness(false, connected),
      { initialProps: { connected: true } },
    );
    view.rerender({ connected: false });
    await act(async () => resolve(configuration()));
    expect(view.result.current).toBe("checking");
    expect(mocks.speech).not.toHaveBeenCalledWith(
      "whisper",
      "small",
      "ja",
      true,
    );
  });
});
