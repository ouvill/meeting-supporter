import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SpeechModelStatusResponse } from "../api/generated/types.gen";
import { useSpeechModel, type SpeechModelLanguage } from "./useSpeechModel";

const sdkMocks = vi.hoisted(() => ({
  getStatus: vi.fn(),
  startDownload: vi.fn(),
  cancelDownload: vi.fn(),
}));

vi.mock("../api/generated/sdk.gen", () => ({
  getSpeechModelStatusApiSttModelGet: sdkMocks.getStatus,
  startSpeechModelDownloadApiSttModelDownloadPost: sdkMocks.startDownload,
  cancelSpeechModelDownloadApiSttModelCancelPost: sdkMocks.cancelDownload,
}));

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function status(
  overrides: Partial<SpeechModelStatusResponse> = {},
): SpeechModelStatusResponse {
  return {
    backend: "vosk",
    model_id: "vosk-small-ja",
    state: "missing",
    phase: "idle",
    language: "ja",
    downloaded_bytes: 0,
    total_bytes: null,
    progress_percent: null,
    model_path: null,
    storage_path: "/app-data/speech",
    error_code: null,
    message: "",
    retryable: true,
    cancelable: false,
    ...overrides,
  };
}

interface SpeechModelApiResult {
  data: SpeechModelStatusResponse;
  error: undefined;
}

function apiResult(data: SpeechModelStatusResponse): SpeechModelApiResult {
  return { data, error: undefined };
}

async function flushReact() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useSpeechModel", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sdkMocks.getStatus.mockReset();
    sdkMocks.startDownload.mockReset();
    sdkMocks.cancelDownload.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("loads the selected language status and clears the save block once the status settles", async () => {
    sdkMocks.getStatus.mockResolvedValue(apiResult(status()));

    const { result } = renderHook(() => useSpeechModel("vosk", null, "ja"));

    expect(result.current.blocksSettingsSave).toBe(true);
    await flushReact();

    expect(sdkMocks.getStatus).toHaveBeenCalledWith(
      expect.objectContaining({
        query: { backend: "vosk", language: "ja" },
      }),
    );
    expect(result.current.status).toMatchObject({
      state: "missing",
      language: "ja",
    });
    expect(result.current.checkingStatus).toBe(false);
    expect(result.current.blocksSettingsSave).toBe(false);
  });

  it("starts one download while the start request remains pending", async () => {
    const start = deferred<SpeechModelApiResult>();
    sdkMocks.getStatus.mockResolvedValue(apiResult(status()));
    sdkMocks.startDownload.mockReturnValue(start.promise);
    const { result } = renderHook(() => useSpeechModel("vosk", null, "ja"));
    await flushReact();

    act(() => {
      void result.current.startDownload();
      void result.current.startDownload();
    });

    expect(sdkMocks.startDownload).toHaveBeenCalledTimes(1);
    expect(result.current.action).toBe("starting");

    await act(async () => {
      start.resolve(
        apiResult(
          status({
            state: "downloading",
            phase: "downloading",
            total_bytes: 100,
            cancelable: true,
          }),
        ),
      );
      await Promise.resolve();
    });

    expect(result.current.status).toMatchObject({
      state: "downloading",
      cancelable: true,
    });
    expect(result.current.action).toBeNull();
  });

  it("polls a download until the managed data is ready", async () => {
    sdkMocks.getStatus
      .mockResolvedValueOnce(
        apiResult(
          status({
            state: "downloading",
            phase: "downloading",
            total_bytes: 100,
            downloaded_bytes: 25,
            cancelable: true,
          }),
        ),
      )
      .mockResolvedValueOnce(
        apiResult(
          status({
            state: "ready",
            phase: "ready",
            model_path: "/app-data/speech/ja",
          }),
        ),
      );

    const { result } = renderHook(() => useSpeechModel("vosk", null, "ja"));
    await flushReact();

    expect(result.current.isDownloading).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });

    expect(sdkMocks.getStatus).toHaveBeenCalledTimes(2);
    expect(result.current.status).toMatchObject({
      state: "ready",
      model_path: "/app-data/speech/ja",
    });
    expect(result.current.blocksSettingsSave).toBe(false);
  });

  it("polls the selected Whisper model and retains its reported download progress", async () => {
    sdkMocks.getStatus
      .mockResolvedValueOnce(
        apiResult(
          status({
            backend: "whisper",
            model_id: "small",
            state: "downloading",
            phase: "downloading",
            progress_percent: 25,
            total_bytes: 100,
            downloaded_bytes: 25,
          }),
        ),
      )
      .mockResolvedValueOnce(
        apiResult(
          status({
            backend: "whisper",
            model_id: "small",
            state: "ready",
            phase: "ready",
            model_path: "/cache/whisper/small",
          }),
        ),
      );

    const { result } = renderHook(() =>
      useSpeechModel("whisper", "small", "ja"),
    );
    await flushReact();

    expect(result.current.status).toMatchObject({
      state: "downloading",
      progress_percent: 25,
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });

    expect(sdkMocks.getStatus).toHaveBeenLastCalledWith(
      expect.objectContaining({
        query: { backend: "whisper", model: "small", language: "ja" },
      }),
    );
    expect(result.current.status).toMatchObject({
      state: "ready",
      model_path: "/cache/whisper/small",
    });
  });

  it("adopts the cancelled status returned by a cancel request", async () => {
    sdkMocks.getStatus.mockResolvedValue(
      apiResult(
        status({
          state: "downloading",
          phase: "downloading",
          total_bytes: 100,
          cancelable: true,
        }),
      ),
    );
    sdkMocks.cancelDownload.mockResolvedValue(
      apiResult(
        status({
          state: "cancelled",
          error_code: "cancelled",
          retryable: true,
        }),
      ),
    );
    const { result } = renderHook(() => useSpeechModel("vosk", null, "ja"));
    await flushReact();

    await act(async () => {
      await result.current.cancelDownload();
    });
    expect(sdkMocks.cancelDownload).toHaveBeenCalledWith(
      expect.objectContaining({
        query: { backend: "vosk", language: "ja" },
      }),
    );

    expect(result.current.status).toMatchObject({
      state: "cancelled",
      error_code: "cancelled",
    });
    expect(result.current.action).toBeNull();
    expect(result.current.blocksSettingsSave).toBe(false);
  });

  it("retries the selected Whisper model with its provider identity", async () => {
    sdkMocks.getStatus.mockResolvedValue(
      apiResult(
        status({
          backend: "whisper",
          model_id: "small",
          state: "failed",
          error_code: "network",
          retryable: true,
        }),
      ),
    );
    sdkMocks.startDownload.mockResolvedValue(
      apiResult(
        status({
          backend: "whisper",
          model_id: "small",
          state: "downloading",
          phase: "downloading",
          total_bytes: 100,
          cancelable: true,
        }),
      ),
    );
    const { result } = renderHook(() =>
      useSpeechModel("whisper", "small", "ja"),
    );
    await flushReact();

    await act(async () => {
      await result.current.startDownload();
    });

    expect(sdkMocks.startDownload).toHaveBeenCalledWith(
      expect.objectContaining({
        body: { backend: "whisper", model: "small", language: "ja" },
      }),
    );
    expect(result.current.status).toMatchObject({
      backend: "whisper",
      model_id: "small",
      state: "downloading",
    });
    expect(result.current.blocksSettingsSave).toBe(true);
  });

  it("aborts an active polling request and leaves no scheduled poll after unmount", async () => {
    const poll = deferred<SpeechModelApiResult>();
    let pollSignal: AbortSignal | undefined;
    sdkMocks.getStatus
      .mockResolvedValueOnce(
        apiResult(
          status({
            state: "downloading",
            phase: "downloading",
            total_bytes: 100,
            cancelable: true,
          }),
        ),
      )
      .mockImplementationOnce((options: { signal: AbortSignal }) => {
        pollSignal = options.signal;
        return poll.promise;
      });

    const { unmount } = renderHook(() => useSpeechModel("vosk", null, "ja"));
    await flushReact();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });

    expect(sdkMocks.getStatus).toHaveBeenCalledTimes(2);
    unmount();

    expect(pollSignal?.aborted).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_400);
    });
    expect(sdkMocks.getStatus).toHaveBeenCalledTimes(2);
  });

  it("ignores a stale status response after the meeting language changes", async () => {
    const japanese = deferred<SpeechModelApiResult>();
    sdkMocks.getStatus
      .mockReturnValueOnce(japanese.promise)
      .mockResolvedValueOnce(apiResult(status({ language: "en" })));

    const initialProps: { language: SpeechModelLanguage } = { language: "ja" };
    const { result, rerender } = renderHook(
      ({ language }) => useSpeechModel("vosk", null, language),
      { initialProps },
    );
    rerender({ language: "en" });
    await flushReact();

    await act(async () => {
      japanese.resolve(
        apiResult(status({ language: "ja", state: "ready", phase: "ready" })),
      );
      await Promise.resolve();
    });

    expect(result.current.language).toBe("en");
    expect(result.current.status).toMatchObject({
      language: "en",
      state: "missing",
    });
  });

  it("does not surface a stale request error after the meeting language changes", async () => {
    const japanese = deferred<SpeechModelApiResult>();
    sdkMocks.getStatus
      .mockReturnValueOnce(japanese.promise)
      .mockResolvedValueOnce(apiResult(status({ language: "en" })));

    const initialProps: { language: SpeechModelLanguage } = { language: "ja" };
    const { result, rerender } = renderHook(
      ({ language }) => useSpeechModel("vosk", null, language),
      { initialProps },
    );
    rerender({ language: "en" });
    await flushReact();

    await act(async () => {
      japanese.reject(new Error("offline"));
      await Promise.resolve();
    });

    expect(result.current.status).toMatchObject({
      language: "en",
      state: "missing",
    });
    expect(result.current.error).toBeNull();
  });

  it("ignores a stale Whisper model response after the selected model changes", async () => {
    const small = deferred<SpeechModelApiResult>();
    sdkMocks.getStatus
      .mockReturnValueOnce(small.promise)
      .mockResolvedValueOnce(
        apiResult(status({ backend: "whisper", model_id: "base" })),
      );

    const initialProps: { model: "small" | "base" } = { model: "small" };
    const { result, rerender } = renderHook(
      ({ model }) => useSpeechModel("whisper", model, "ja"),
      { initialProps },
    );
    rerender({ model: "base" });
    await flushReact();

    await act(async () => {
      small.resolve(
        apiResult(
          status({
            backend: "whisper",
            model_id: "small",
            state: "ready",
            phase: "ready",
          }),
        ),
      );
      await Promise.resolve();
    });

    expect(result.current.model).toBe("base");
    expect(result.current.status).toMatchObject({
      backend: "whisper",
      model_id: "base",
      state: "missing",
    });
  });
  it("ignores a stale Vosk response after switching to a Whisper model", async () => {
    const vosk = deferred<SpeechModelApiResult>();
    sdkMocks.getStatus
      .mockReturnValueOnce(vosk.promise)
      .mockResolvedValueOnce(
        apiResult(status({ backend: "whisper", model_id: "small" })),
      );

    const initialProps: { backend: "vosk" | "whisper"; model: null | "small" } =
      {
        backend: "vosk",
        model: null,
      };
    const { result, rerender } = renderHook(
      ({ backend, model }) => useSpeechModel(backend, model, "ja"),
      { initialProps },
    );
    rerender({ backend: "whisper", model: "small" });
    await flushReact();

    await act(async () => {
      vosk.resolve(
        apiResult(
          status({
            state: "ready",
            phase: "ready",
            model_path: "/app-data/vosk",
          }),
        ),
      );
      await Promise.resolve();
    });

    expect(result.current.backend).toBe("whisper");
    expect(result.current.status).toMatchObject({
      backend: "whisper",
      model_id: "small",
      state: "missing",
    });
  });
});
