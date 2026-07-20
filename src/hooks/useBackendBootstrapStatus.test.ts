import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useBackendBootstrapStatus } from "./useBackendBootstrapStatus";

// ---------------------------------------------------------------------------
// Mock @tauri-apps/api/core so the client module can be imported in jsdom.
// vi.mock is hoisted above imports, so all downstream modules see the mock.
// ---------------------------------------------------------------------------
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";

const mockInvoke = vi.mocked(invoke);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Advance fake timers and flush pending microtasks / React updates. */
async function flushAsync(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const DEFAULT_BOOTSTRAP = {
  phase: "initializing",
  message: "Pythonバックエンドを起動しています...",
};

const POLL_INTERVAL_MS = 1200;

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useBackendBootstrapStatus", () => {
  beforeEach(() => {
    vi.useFakeTimers();

    // Default mock responses: backend not yet ready
    mockInvoke.mockImplementation((cmd: string) => {
      switch (cmd) {
        case "get_backend_bootstrap_status":
          return Promise.resolve({
            phase: "starting",
            message: "Starting backend...",
          });
        case "is_backend_running":
          return Promise.resolve(false);
        case "get_api_port":
          return Promise.resolve(null);
        case "get_api_auth_token":
          return Promise.resolve(null);
        case "get_backend_crash_info":
          return Promise.resolve(null);
        default:
          return Promise.reject(new Error(`Unknown command: ${cmd}`));
      }
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  // -----------------------------------------------------------------------
  // 1. 初期状態
  // -----------------------------------------------------------------------
  it("returns initial state before any poll completes", () => {
    // Keep refresh pending indefinitely
    mockInvoke.mockReturnValue(
      new Promise<never>(() => {
        /* never resolves */
      }),
    );

    const { result } = renderHook(() => useBackendBootstrapStatus());

    expect(result.current.apiPort).toBeNull();
    expect(result.current.bootstrap).toEqual(DEFAULT_BOOTSTRAP);
  });

  // -----------------------------------------------------------------------
  // 2. running+port で apiPort 設定
  // -----------------------------------------------------------------------
  it("sets apiPort when backend is running and a port is provided", async () => {
    mockInvoke.mockImplementation((cmd: string) => {
      switch (cmd) {
        case "get_backend_bootstrap_status":
          return Promise.resolve({ phase: "ready", message: "Backend ready" });
        case "is_backend_running":
          return Promise.resolve(true);
        case "get_api_port":
          return Promise.resolve(8000);
        case "get_api_auth_token":
          return Promise.resolve("token");
        case "get_backend_crash_info":
          return Promise.resolve(null);
        default:
          return Promise.reject(new Error(`Unknown command: ${cmd}`));
      }
    });

    const { result } = renderHook(() => useBackendBootstrapStatus());

    // Flush microtasks (mock promise resolutions) & React updates
    await flushAsync();

    expect(result.current.apiPort).toBe(8000);
    expect(result.current.bootstrap).toEqual({
      phase: "ready",
      message: "Backend ready",
    });
    expect(result.current.apiAuthToken).toBe("token");
  });

  // -----------------------------------------------------------------------
  // 3. polling が繰り返される
  // -----------------------------------------------------------------------
  it("polls repeatedly at the configured interval", async () => {
    renderHook(() => useBackendBootstrapStatus());

    // Let the initial refresh complete
    await flushAsync();
    const callsAfterFirst = mockInvoke.mock.calls.length;
    expect(callsAfterFirst).toBe(5);

    // Advance one interval — wrapped in act so React sees the state updates
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(mockInvoke.mock.calls.length).toBe(callsAfterFirst + 5);

    // Advance another interval
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(mockInvoke.mock.calls.length).toBe(callsAfterFirst + 10);
  });

  // -----------------------------------------------------------------------
  // 4. unmount cleanup (interval 解除 + cancelled flag)
  // -----------------------------------------------------------------------
  it("stops polling after unmount", async () => {
    const { unmount } = renderHook(() => useBackendBootstrapStatus());

    // Let the initial refresh complete
    await flushAsync();
    const callsBeforeUnmount = mockInvoke.mock.calls.length;

    // Unmount — should clear the interval
    unmount();

    // Advance well past the interval — no new invocations should happen
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 10);
    });

    expect(mockInvoke.mock.calls.length).toBe(callsBeforeUnmount);
  });

  // -----------------------------------------------------------------------
  // 5. ログ抑制 — expected bootstrap errors (not found / backend not ready)
  // -----------------------------------------------------------------------
  it('suppresses expected errors containing "not found"', async () => {
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mockInvoke.mockRejectedValue(new Error("Command not found"));

    renderHook(() => useBackendBootstrapStatus());
    await flushAsync();

    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });

  it('suppresses expected "Backend not ready" errors', async () => {
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mockInvoke.mockRejectedValue(new Error("Backend not ready yet"));

    renderHook(() => useBackendBootstrapStatus());
    await flushAsync();

    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });

  it('does NOT suppress arbitrary errors that merely contain "backend"', async () => {
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mockInvoke.mockRejectedValue(new Error("Backend serialization error"));

    renderHook(() => useBackendBootstrapStatus());
    await flushAsync();

    expect(consoleSpy).toHaveBeenCalledWith(
      "Bootstrap refresh failed:",
      expect.any(Error),
    );
    consoleSpy.mockRestore();
  });

  // -----------------------------------------------------------------------
  // 6. エラー処理 — unexpected errors are logged
  // -----------------------------------------------------------------------
  it("logs unexpected errors to console", async () => {
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mockInvoke.mockRejectedValue(new Error("Network timeout"));

    renderHook(() => useBackendBootstrapStatus());
    await flushAsync();

    expect(consoleSpy).toHaveBeenCalledWith(
      "Bootstrap refresh failed:",
      expect.any(Error),
    );
    consoleSpy.mockRestore();
  });

  // -----------------------------------------------------------------------
  // 7. apiPort が null のまま (running=false の場合)
  // -----------------------------------------------------------------------
  it("does not set apiPort when backend is not running even if port is available", async () => {
    mockInvoke.mockImplementation((cmd: string) => {
      switch (cmd) {
        case "get_backend_bootstrap_status":
          return Promise.resolve({ phase: "waiting", message: "Waiting..." });
        case "is_backend_running":
          return Promise.resolve(false);
        case "get_api_port":
          return Promise.resolve(8000); // port is available but backend not running
        case "get_api_auth_token":
          return Promise.resolve("token");
        case "get_backend_crash_info":
          return Promise.resolve(null);
        default:
          return Promise.reject(new Error(`Unknown command: ${cmd}`));
      }
    });

    const { result } = renderHook(() => useBackendBootstrapStatus());
    await flushAsync();

    // Even though port is returned, running is false, so apiPort stays null
    expect(result.current.apiPort).toBeNull();
  });

  // -----------------------------------------------------------------------
  // 8. runtime validation — getBackendBootstrapStatus fallback on invalid shape
  // -----------------------------------------------------------------------
  it("falls back to default bootstrap status when IPC returns invalid shape", async () => {
    const consoleWarnSpy = vi
      .spyOn(console, "warn")
      .mockImplementation(() => {});
    mockInvoke.mockImplementation((cmd: string) => {
      switch (cmd) {
        case "get_backend_bootstrap_status":
          // phase / message are the wrong types — Zod schema rejects these
          return Promise.resolve({ phase: 123, message: null });
        case "is_backend_running":
          return Promise.resolve(false);
        case "get_api_port":
          return Promise.resolve(null);
        case "get_api_auth_token":
          return Promise.resolve(null);
        case "get_backend_crash_info":
          return Promise.resolve(null);
        default:
          return Promise.reject(new Error(`Unknown command: ${cmd}`));
      }
    });

    const { result } = renderHook(() => useBackendBootstrapStatus());
    await flushAsync();

    // Fallback default should be used
    expect(result.current.bootstrap).toEqual(DEFAULT_BOOTSTRAP);
    // console.warn should have been called by the client validation layer
    expect(consoleWarnSpy).toHaveBeenCalledWith(
      "[BootstrapClient] Invalid bootstrap status shape:",
      expect.any(Error),
    );
    consoleWarnSpy.mockRestore();
  });

  // -----------------------------------------------------------------------
  // 9. crash info — unexpected termination overrides bootstrap to 'failed'
  // -----------------------------------------------------------------------
  it("sets bootstrap to failed and clears apiPort when crash info reports unexpected termination", async () => {
    const crashData = {
      unexpected: true,
      exit_code: 137,
      signal: null,
      message: "Backend process terminated unexpectedly. Exited with code 137.",
    };
    mockInvoke.mockImplementation((cmd: string) => {
      switch (cmd) {
        case "get_backend_bootstrap_status":
          return Promise.resolve({
            phase: "running",
            message: "Backend is ready.",
          });
        case "is_backend_running":
          return Promise.resolve(true);
        case "get_api_port":
          return Promise.resolve(8000);
        case "get_api_auth_token":
          return Promise.resolve("token");
        case "get_backend_crash_info":
          return Promise.resolve(crashData);
        default:
          return Promise.reject(new Error(`Unknown command: ${cmd}`));
      }
    });

    const { result } = renderHook(() => useBackendBootstrapStatus());
    await flushAsync();

    expect(result.current.bootstrap.phase).toBe("failed");
    expect(result.current.bootstrap.message).toContain("unexpectedly");
    expect(result.current.apiPort).toBeNull();
    expect(result.current.apiAuthToken).toBeNull();
    expect(result.current.crashInfo).toEqual(crashData);
  });

  // -----------------------------------------------------------------------
  // 9b. crash detected → polling stops (no further IPC calls)
  // -----------------------------------------------------------------------
  it("stops polling after an unexpected crash is detected", async () => {
    const crashData = {
      unexpected: true,
      exit_code: 137,
      signal: null,
      message: "Backend process terminated unexpectedly. Exited with code 137.",
    };
    mockInvoke.mockImplementation((cmd: string) => {
      switch (cmd) {
        case "get_backend_bootstrap_status":
          return Promise.resolve({
            phase: "running",
            message: "Backend is running.",
          });
        case "is_backend_running":
          return Promise.resolve(false);
        case "get_api_port":
          return Promise.resolve(null);
        case "get_api_auth_token":
          return Promise.resolve(null);
        case "get_backend_crash_info":
          return Promise.resolve(crashData);
        default:
          return Promise.reject(new Error(`Unknown command: ${cmd}`));
      }
    });

    renderHook(() => useBackendBootstrapStatus());

    // Let the initial refresh complete
    await flushAsync();
    const callsAfterFirst = mockInvoke.mock.calls.length;
    expect(callsAfterFirst).toBe(5);

    // Advance well past multiple poll intervals — no new IPC calls should happen
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 10);
    });

    expect(mockInvoke.mock.calls.length).toBe(callsAfterFirst);
  });

  // -----------------------------------------------------------------------
  // 10. crash info — null crash info does not affect normal flow
  // -----------------------------------------------------------------------
  it("returns null crashInfo when no crash detected", async () => {
    mockInvoke.mockImplementation((cmd: string) => {
      switch (cmd) {
        case "get_backend_bootstrap_status":
          return Promise.resolve({
            phase: "running",
            message: "Backend is ready.",
          });
        case "is_backend_running":
          return Promise.resolve(true);
        case "get_api_port":
          return Promise.resolve(8000);
        case "get_api_auth_token":
          return Promise.resolve("token");
        case "get_backend_crash_info":
          return Promise.resolve(null);
        default:
          return Promise.reject(new Error(`Unknown command: ${cmd}`));
      }
    });

    const { result } = renderHook(() => useBackendBootstrapStatus());
    await flushAsync();

    expect(result.current.crashInfo).toBeNull();
    expect(result.current.bootstrap.phase).toBe("running");
    expect(result.current.apiPort).toBe(8000);
    expect(result.current.apiAuthToken).toBe("token");
  });

  // -----------------------------------------------------------------------
  // 11. crash info — invalid shape falls back to null
  // -----------------------------------------------------------------------
  it("falls back to null crashInfo when IPC returns invalid shape", async () => {
    const consoleWarnSpy = vi
      .spyOn(console, "warn")
      .mockImplementation(() => {});
    mockInvoke.mockImplementation((cmd: string) => {
      switch (cmd) {
        case "get_backend_bootstrap_status":
          return Promise.resolve({ phase: "starting", message: "Starting..." });
        case "is_backend_running":
          return Promise.resolve(false);
        case "get_api_port":
          return Promise.resolve(null);
        case "get_api_auth_token":
          return Promise.resolve(null);
        case "get_backend_crash_info":
          // Invalid shape — missing required fields
          return Promise.resolve({ unexpected: "yes" });
        default:
          return Promise.reject(new Error(`Unknown command: ${cmd}`));
      }
    });

    const { result } = renderHook(() => useBackendBootstrapStatus());
    await flushAsync();

    expect(result.current.crashInfo).toBeNull();
    expect(consoleWarnSpy).toHaveBeenCalledWith(
      "[BootstrapClient] Invalid crash info shape:",
      expect.any(Error),
    );
    consoleWarnSpy.mockRestore();
  });
});
