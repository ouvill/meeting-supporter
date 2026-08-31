import { StrictMode, createElement, type ReactNode } from "react";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useBackendBootstrapStatus } from "./useBackendBootstrapStatus";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";

const mockInvoke = vi.mocked(invoke);
const POLL_INTERVAL_MS = 1200;
const DEFAULT_BOOTSTRAP = {
  phase: "initializing",
  message: "Pythonバックエンドを起動しています...",
};

interface SnapshotPayload {
  phase: string;
  message: string;
  running: boolean;
  port: number | null;
  auth_token: string | null;
  crash: {
    unexpected: boolean;
    exit_code: number | null;
    signal: number | null;
    message: string;
  } | null;
}

function snapshot(
  overrides: Partial<SnapshotPayload> = {},
): SnapshotPayload {
  return {
    phase: "starting",
    message: "Starting backend...",
    running: false,
    port: null,
    auth_token: null,
    crash: null,
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function flushAsync() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

function StrictWrapper({ children }: { children: ReactNode }) {
  return createElement(StrictMode, null, children);
}

describe("useBackendBootstrapStatus", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockInvoke.mockReset();
    mockInvoke.mockResolvedValue(snapshot());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("obtains the initial state with one coherent snapshot IPC", async () => {
    const { result } = renderHook(() => useBackendBootstrapStatus());

    expect(result.current.bootstrap).toEqual(DEFAULT_BOOTSTRAP);
    await flushAsync();

    expect(mockInvoke).toHaveBeenCalledTimes(1);
    expect(mockInvoke).toHaveBeenCalledWith("get_backend_bootstrap_snapshot");
    expect(result.current.bootstrap).toEqual({
      phase: "starting",
      message: "Starting backend...",
    });
  });

  it("deduplicates the initial request across Strict Mode effect replay", async () => {
    const pending = deferred<SnapshotPayload>();
    mockInvoke.mockReturnValue(pending.promise);

    renderHook(() => useBackendBootstrapStatus(), { wrapper: StrictWrapper });

    expect(mockInvoke).toHaveBeenCalledTimes(1);
    pending.resolve(snapshot());
    await flushAsync();
  });

  it("publishes ready credentials from the first snapshot and keeps liveness polling", async () => {
    mockInvoke.mockResolvedValue(
      snapshot({
        phase: "running",
        message: "Backend is ready.",
        running: true,
        port: 49152,
        auth_token: "local-capability",
      }),
    );

    const { result } = renderHook(() => useBackendBootstrapStatus());
    await flushAsync();

    expect(result.current).toMatchObject({
      apiPort: 49152,
      apiAuthToken: "local-capability",
      bootstrap: { phase: "running", message: "Backend is ready." },
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(mockInvoke).toHaveBeenCalledTimes(2);
  });

  it("surfaces a backend crash detected after credentials were ready", async () => {
    const crash = {
      unexpected: true,
      exit_code: 137,
      signal: null,
      message: "Backend process terminated unexpectedly. Exited with code 137.",
    };
    mockInvoke
      .mockResolvedValueOnce(
        snapshot({
          phase: "running",
          message: "Backend is ready.",
          running: true,
          port: 49152,
          auth_token: "local-capability",
        }),
      )
      .mockResolvedValueOnce(
        snapshot({ phase: "running", running: false, crash }),
      );

    const { result } = renderHook(() => useBackendBootstrapStatus());
    await flushAsync();
    expect(result.current.apiPort).toBe(49152);
    expect(result.current.apiAuthToken).toBe("local-capability");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });

    expect(result.current.bootstrap).toEqual({
      phase: "failed",
      message: crash.message,
    });
    expect(result.current.crashInfo).toEqual(crash);
    expect(result.current.apiPort).toBeNull();
    expect(result.current.apiAuthToken).toBeNull();
    expect(mockInvoke).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 5);
    });
    expect(mockInvoke).toHaveBeenCalledTimes(2);
  });

  it("never overlaps a slow snapshot with another poll", async () => {
    const first = deferred<SnapshotPayload>();
    mockInvoke
      .mockReturnValueOnce(first.promise)
      .mockResolvedValue(snapshot({ phase: "syncing" }));

    renderHook(() => useBackendBootstrapStatus());
    expect(mockInvoke).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 10);
    });
    expect(mockInvoke).toHaveBeenCalledTimes(1);

    first.resolve(snapshot());
    await flushAsync();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(mockInvoke).toHaveBeenCalledTimes(2);
  });

  it("continues sequential polling while the backend is not ready", async () => {
    renderHook(() => useBackendBootstrapStatus());
    await flushAsync();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2);
    });

    expect(mockInvoke).toHaveBeenCalledTimes(3);
    expect(
      mockInvoke.mock.calls.every(
        ([command]) => command === "get_backend_bootstrap_snapshot",
      ),
    ).toBe(true);
  });

  it("surfaces an unexpected crash, clears credentials, and stops polling", async () => {
    const crash = {
      unexpected: true,
      exit_code: 137,
      signal: null,
      message: "Backend process terminated unexpectedly. Exited with code 137.",
    };
    mockInvoke.mockResolvedValue(
      snapshot({
        phase: "running",
        running: false,
        crash,
      }),
    );

    const { result } = renderHook(() => useBackendBootstrapStatus());
    await flushAsync();

    expect(result.current.bootstrap).toEqual({
      phase: "failed",
      message: crash.message,
    });
    expect(result.current.crashInfo).toEqual(crash);
    expect(result.current.apiPort).toBeNull();
    expect(result.current.apiAuthToken).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 10);
    });
    expect(mockInvoke).toHaveBeenCalledTimes(1);
  });

  it("fails closed when any part of the snapshot payload is invalid", async () => {
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    mockInvoke.mockResolvedValue({
      phase: "running",
      message: "Backend ready",
      running: true,
      port: 49152,
      auth_token: "local-capability",
      crash: { unexpected: "yes" },
    });

    const { result } = renderHook(() => useBackendBootstrapStatus());
    await flushAsync();

    expect(result.current.apiPort).toBeNull();
    expect(result.current.apiAuthToken).toBeNull();
    expect(result.current.bootstrap).toEqual(DEFAULT_BOOTSTRAP);
    expect(consoleWarn).toHaveBeenCalledWith(
      "[BootstrapClient] Invalid bootstrap snapshot shape:",
      expect.any(Error),
    );
  });

  it("retains expected-error suppression and unexpected-error reporting", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    mockInvoke.mockRejectedValueOnce(new Error("Backend not ready yet"));

    renderHook(() => useBackendBootstrapStatus());
    await flushAsync();
    expect(consoleError).not.toHaveBeenCalled();

    mockInvoke.mockRejectedValueOnce(new Error("Backend serialization error"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(consoleError).toHaveBeenCalledWith(
      "Bootstrap refresh failed:",
      expect.any(Error),
    );
  });

  it("does not publish a pending result or schedule another poll after unmount", async () => {
    const pending = deferred<SnapshotPayload>();
    mockInvoke.mockReturnValue(pending.promise);
    const { result, unmount } = renderHook(() => useBackendBootstrapStatus());

    unmount();
    pending.resolve(
      snapshot({
        running: true,
        port: 49152,
        auth_token: "local-capability",
      }),
    );
    await flushAsync();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2);
    });

    expect(result.current.apiPort).toBeNull();
    expect(mockInvoke).toHaveBeenCalledTimes(1);
  });
});
