import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SpeechModelStatusResponse } from "../api/generated/types.gen";
import { useSpeechModel, type SpeechModelLanguage } from "./useSpeechModel";
import { resetSpeechModelTasks } from "../store/speechModelStore";
import { useSettingsTaskStore } from "../store/settingsTaskStore";

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
    backend: "whisper",
    model_id: "large-v3-turbo",
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
    resetSpeechModelTasks();
    useSettingsTaskStore.setState({ tasks: {} });
    vi.useFakeTimers();
    sdkMocks.getStatus.mockReset();
    sdkMocks.startDownload.mockReset();
    sdkMocks.cancelDownload.mockReset();
  });

  afterEach(() => {
    cleanup();
    resetSpeechModelTasks();
    vi.useRealTimers();
  });

  it("loads the selected language status and clears the save block once the status settles", async () => {
    sdkMocks.getStatus.mockResolvedValue(apiResult(status()));

    const { result } = renderHook(() => useSpeechModel("whisper", null, "ja"));

    expect(result.current.blocksSettingsSave).toBe(true);
    await flushReact();

    expect(sdkMocks.getStatus).toHaveBeenCalledWith(
      expect.objectContaining({
        query: { backend: "whisper", language: "ja" },
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
    const { result } = renderHook(() => useSpeechModel("whisper", null, "ja"));
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

    const { result } = renderHook(() => useSpeechModel("whisper", null, "ja"));
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
    const { result } = renderHook(() => useSpeechModel("whisper", null, "ja"));
    await flushReact();

    await act(async () => {
      await result.current.cancelDownload();
    });
    expect(sdkMocks.cancelDownload).toHaveBeenCalledWith(
      expect.objectContaining({
        query: { backend: "whisper", language: "ja" },
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

  it("continues polling after unmount and reports completion without an open settings screen", async () => {
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

    const { unmount } = renderHook(() => useSpeechModel("whisper", null, "ja"));
    await flushReact();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });

    expect(sdkMocks.getStatus).toHaveBeenCalledTimes(2);
    unmount();

    expect(pollSignal?.aborted).toBe(false);
    await act(async () => {
      poll.resolve(apiResult(status({ state: "ready", phase: "ready" })));
    });
    expect(Object.values(useSettingsTaskStore.getState().tasks)).toEqual([
      expect.objectContaining({
        state: "completed",
        message: expect.stringContaining("取得が完了しました"),
      }),
    ]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_400);
    });
    expect(sdkMocks.getStatus).toHaveBeenCalledTimes(2);
  });

  it("retains a pending start across close and reopen and prevents a duplicate download", async () => {
    const start = deferred<SpeechModelApiResult>();
    let signal: AbortSignal | undefined;
    sdkMocks.getStatus.mockResolvedValue(apiResult(status()));
    sdkMocks.startDownload.mockImplementation(
      (options: { signal: AbortSignal }) => {
        signal = options.signal;
        return start.promise;
      },
    );
    const first = renderHook(() => useSpeechModel("whisper", null, "ja"));
    await flushReact();
    act(() => {
      void first.result.current.startDownload();
    });
    first.unmount();
    expect(signal?.aborted).toBe(false);

    const reopened = renderHook(() => useSpeechModel("whisper", null, "ja"));
    expect(reopened.result.current.action).toBe("starting");
    act(() => {
      void reopened.result.current.startDownload();
    });
    expect(sdkMocks.startDownload).toHaveBeenCalledOnce();
    await act(async () => {
      start.resolve(
        apiResult(status({ state: "downloading", progress_percent: 40 })),
      );
    });
    expect(reopened.result.current.status?.progress_percent).toBe(40);
    expect(reopened.result.current.isDownloading).toBe(true);
  });

  it("keeps an earlier model download running when another model is selected", async () => {
    sdkMocks.getStatus.mockImplementation(async ({ query }) =>
      apiResult(status({ model_id: query.model })),
    );
    sdkMocks.startDownload.mockResolvedValue(
      apiResult(status({ model_id: "small", state: "downloading" })),
    );
    const { result, rerender } = renderHook(
      ({ model }: { model: "small" | "base" }) =>
        useSpeechModel("whisper", model, "ja"),
      { initialProps: { model: "small" as "small" | "base" } },
    );
    await flushReact();
    await act(async () => {
      await result.current.startDownload();
    });
    rerender({ model: "base" });
    await flushReact();
    sdkMocks.getStatus.mockImplementation(async ({ query }) =>
      apiResult(
        status({
          model_id: query.model,
          state: query.model === "small" ? "ready" : "missing",
        }),
      ),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });
    expect(result.current.status).toMatchObject({
      model_id: "base",
      state: "missing",
    });
    expect(
      useSettingsTaskStore.getState().tasks["speech:whisper:small:ja"].state,
    ).toBe("completed");
  });

  it("reconciles an uncertain start and retries polling errors while settings is closed", async () => {
    sdkMocks.getStatus.mockResolvedValueOnce(apiResult(status()));
    sdkMocks.startDownload.mockRejectedValue(new Error("offline"));
    const { result, unmount } = renderHook(() =>
      useSpeechModel("whisper", null, "ja"),
    );
    await flushReact();
    await act(async () => {
      await result.current.startDownload();
    });
    expect(result.current.confirmingStart).toBe(true);
    unmount();
    sdkMocks.getStatus
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(apiResult(status({ state: "downloading" })))
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(
        apiResult(status({ state: "failed", message: "容量不足です。" })),
      );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_400);
    });
    expect(
      useSettingsTaskStore.getState().tasks["speech:whisper::ja"].state,
    ).toBe("running");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });
    expect(
      useSettingsTaskStore.getState().tasks["speech:whisper::ja"],
    ).toMatchObject({
      state: "failed",
      message: expect.stringContaining("容量不足です。"),
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_400);
    });
    expect(sdkMocks.getStatus).toHaveBeenCalledTimes(5);
  });

  it("only aborts an idle status read when settings closes", async () => {
    const pending = deferred<SpeechModelApiResult>();
    let signal: AbortSignal | undefined;
    sdkMocks.getStatus.mockImplementation(
      (options: { signal: AbortSignal }) => {
        signal = options.signal;
        return pending.promise;
      },
    );
    const { unmount } = renderHook(() => useSpeechModel("whisper", null, "ja"));
    unmount();
    expect(signal?.aborted).toBe(true);
    expect(sdkMocks.cancelDownload).not.toHaveBeenCalled();
  });

  it("ignores a stale status response after the meeting language changes", async () => {
    const japanese = deferred<SpeechModelApiResult>();
    sdkMocks.getStatus
      .mockReturnValueOnce(japanese.promise)
      .mockResolvedValueOnce(apiResult(status({ language: "en" })));

    const initialProps: { language: SpeechModelLanguage } = { language: "ja" };
    const { result, rerender } = renderHook(
      ({ language }) => useSpeechModel("whisper", null, language),
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
      ({ language }) => useSpeechModel("whisper", null, language),
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
  it("ignores a stale model response after switching Whisper quality", async () => {
    const previous = deferred<SpeechModelApiResult>();
    sdkMocks.getStatus
      .mockReturnValueOnce(previous.promise)
      .mockResolvedValueOnce(
        apiResult(status({ backend: "whisper", model_id: "small" })),
      );

    const initialProps: {
      backend: "whisper" | "whisper";
      model: null | "small";
    } = {
      backend: "whisper",
      model: null,
    };
    const { result, rerender } = renderHook(
      ({ backend, model }) => useSpeechModel(backend, model, "ja"),
      { initialProps },
    );
    rerender({ backend: "whisper", model: "small" });
    await flushReact();

    await act(async () => {
      previous.resolve(
        apiResult(
          status({
            state: "ready",
            phase: "ready",
            model_path: "/cache/previous",
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
