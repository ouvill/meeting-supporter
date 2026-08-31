import { useEffect, useRef, useState } from "react";
import {
  getBackendBootstrapSnapshot,
  isExpectedBootstrapError,
  type BackendBootstrapSnapshot,
  type BackendCrashInfo,
  type BootstrapStatus,
} from "../platform/backendBootstrapClient";

const INITIAL_BOOTSTRAP: BootstrapStatus = {
  phase: "initializing",
  message: "Pythonバックエンドを起動しています...",
};

const POLL_INTERVAL_MS = 1200;

export type { BootstrapStatus, BackendCrashInfo };

export interface BackendBootstrapState {
  apiPort: number | null;
  apiAuthToken: string | null;
  bootstrap: BootstrapStatus;
  crashInfo: BackendCrashInfo | null;
}

/** Polls sequential bootstrap snapshots until credentials or a crash are available. */
export function useBackendBootstrapStatus(): BackendBootstrapState {
  const [apiPort, setApiPort] = useState<number | null>(null);
  const [apiAuthToken, setApiAuthToken] = useState<string | null>(null);
  const [bootstrap, setBootstrap] =
    useState<BootstrapStatus>(INITIAL_BOOTSTRAP);
  const [crashInfo, setCrashInfo] = useState<BackendCrashInfo | null>(null);
  const pendingSnapshot = useRef<Promise<BackendBootstrapSnapshot> | null>(null);

  useEffect(() => {
    let cancelled = false;
    let stopped = false;
    let timeoutId: number | null = null;

    function stopPolling(): void {
      stopped = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
        timeoutId = null;
      }
    }

    function scheduleNextPoll(): void {
      if (cancelled || stopped || timeoutId !== null) return;
      timeoutId = window.setTimeout(() => {
        timeoutId = null;
        void refresh();
      }, POLL_INTERVAL_MS);
    }

    async function refresh(): Promise<void> {
      const request =
        pendingSnapshot.current ?? getBackendBootstrapSnapshot();
      pendingSnapshot.current = request;

      try {
        const snapshot = await request;
        if (cancelled) return;

        setCrashInfo(snapshot.crash);
        if (snapshot.crash?.unexpected) {
          setBootstrap({
            phase: "failed",
            message: snapshot.crash.message,
          });
          setApiPort(null);
          setApiAuthToken(null);
          stopPolling();
          return;
        }

        setBootstrap({ phase: snapshot.phase, message: snapshot.message });
        if (
          snapshot.running &&
          snapshot.port !== null &&
          snapshot.auth_token !== null
        ) {
          setApiPort(snapshot.port);
          setApiAuthToken(snapshot.auth_token);
        } else {
          setApiPort(null);
          setApiAuthToken(null);
        }
      } catch (err: unknown) {
        if (!cancelled && !isExpectedBootstrapError(err)) {
          console.error("Bootstrap refresh failed:", err);
        }
      } finally {
        if (pendingSnapshot.current === request) {
          pendingSnapshot.current = null;
        }
        scheduleNextPoll();
      }
    }

    void refresh();
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, []);

  return { apiPort, apiAuthToken, bootstrap, crashInfo };
}
