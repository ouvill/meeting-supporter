import { useEffect, useState } from "react";
import {
  getBackendBootstrapStatus,
  getApiPort,
  getApiAuthToken,
  isBackendRunning,
  isExpectedBootstrapError,
  type BootstrapStatus,
} from "../platform/backendBootstrapClient";

const INITIAL_BOOTSTRAP: BootstrapStatus = {
  phase: "initializing",
  message: "バックエンドを起動しています...",
};

const POLL_INTERVAL_MS = 1200;

export type { BootstrapStatus };

export interface BackendBootstrapState {
  apiPort: number | null;
  apiAuthToken: string | null;
  bootstrap: BootstrapStatus;
}

/**
 * Polls the Tauri backend bootstrap IPC until the backend is ready.
 *
 * Returns the API port (once determined), the current bootstrap status
 * (phase / message) that drives the bootstrap screen.
 *
 * The effect cleans up its interval and sets a cancelled flag to prevent
 * state updates after unmount.
 */
export function useBackendBootstrapStatus(): BackendBootstrapState {
  const [apiPort, setApiPort] = useState<number | null>(null);
  const [apiAuthToken, setApiAuthToken] = useState<string | null>(null);
  const [bootstrap, setBootstrap] =
    useState<BootstrapStatus>(INITIAL_BOOTSTRAP);

  useEffect(() => {
    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | null = null;

    /** Stop the polling loop (idempotent). */
    function stopPolling(): void {
      if (intervalId !== null) {
        clearInterval(intervalId);
        intervalId = null;
      }
    }

    async function refresh(): Promise<void> {
      try {
        const status = await getBackendBootstrapStatus();
        const running = await isBackendRunning();
        const port = await getApiPort();
        const token = await getApiAuthToken();
        if (cancelled) return;
        setBootstrap(status);
        if (running && port && token) {
          setApiPort(port);
          setApiAuthToken(token);
        } else {
          setApiPort(null);
          setApiAuthToken(null);
        }
        if (status.phase === "failed") stopPolling();
      } catch (err: unknown) {
        if (!isExpectedBootstrapError(err)) {
          console.error("Bootstrap refresh failed:", err);
        }
      }
    }

    refresh();
    intervalId = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, []);

  return { apiPort, apiAuthToken, bootstrap };
}
