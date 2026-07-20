import { useCallback, useEffect, useRef, useState } from "react";
import {
  onCurrentWindowFocused,
  readWindowAlwaysOnTop,
  setWindowAlwaysOnTop,
} from "../platform/tauriWindow";

export const ASSISTANT_ALWAYS_ON_TOP_KEY =
  "meeting-supporter.assistant-always-on-top";

const READBACK_ATTEMPTS = 8;
const READBACK_DELAY_MS = 50;

async function readBackDesiredState(desired: boolean): Promise<boolean | null> {
  let readback = await readWindowAlwaysOnTop();
  for (
    let attempt = 1;
    attempt < READBACK_ATTEMPTS && readback !== desired;
    attempt += 1
  ) {
    await new Promise<void>((resolve) => {
      window.setTimeout(resolve, READBACK_DELAY_MS);
    });
    readback = await readWindowAlwaysOnTop();
  }
  return readback;
}

export type AlwaysOnTopActual = "unknown" | "on" | "off";
export type AlwaysOnTopIssue = "window" | "storage" | null;

export interface AlwaysOnTopController {
  actual: AlwaysOnTopActual;
  busy: boolean;
  issue: AlwaysOnTopIssue;
  statusMessage: string | null;
  toggle: () => Promise<void>;
  retry: () => Promise<void>;
}

interface UseAlwaysOnTopOptions {
  defaultDesired: boolean;
  storageKey?: string;
}

function readDesiredPreference(
  storageKey: string | undefined,
  defaultDesired: boolean,
): boolean {
  if (!storageKey) return defaultDesired;
  try {
    const stored = window.localStorage.getItem(storageKey);
    if (stored === "true") return true;
    if (stored === "false") return false;
    return defaultDesired;
  } catch {
    return defaultDesired;
  }
}

function toActual(value: boolean | null): AlwaysOnTopActual {
  return value === null ? "unknown" : value ? "on" : "off";
}

export function useAlwaysOnTop({
  defaultDesired,
  storageKey,
}: UseAlwaysOnTopOptions): AlwaysOnTopController {
  const initialDesiredRef = useRef(
    readDesiredPreference(storageKey, defaultDesired),
  );
  const desiredRef = useRef(initialDesiredRef.current);
  const operationRef = useRef(0);
  const mountedRef = useRef(true);
  const needsApplyOnFocusRef = useRef(true);
  const [actual, setActual] = useState<AlwaysOnTopActual>("unknown");
  const [busy, setBusy] = useState(true);
  const [issue, setIssue] = useState<AlwaysOnTopIssue>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(
    "前面固定を確認しています",
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const applyDesired = useCallback(
    async (desired: boolean) => {
      const operation = ++operationRef.current;
      if (mountedRef.current) {
        setBusy(true);
        setIssue(null);
        setStatusMessage("前面固定を確認しています");
      }

      try {
        await setWindowAlwaysOnTop(desired);
        if (!mountedRef.current || operation !== operationRef.current) return;
        const readback = await readBackDesiredState(desired);
        if (!mountedRef.current || operation !== operationRef.current) return;

        setActual(toActual(readback));
        if (readback === null || readback !== desired) {
          setIssue("window");
          setStatusMessage("前面固定の状態を確認できませんでした");
          needsApplyOnFocusRef.current = true;
          return;
        }

        needsApplyOnFocusRef.current = false;
        desiredRef.current = desired;
        if (storageKey) {
          try {
            window.localStorage.setItem(storageKey, String(desired));
          } catch {
            setIssue("storage");
            setStatusMessage(
              "前面固定は変更しましたが、次回起動用の保存に失敗しました",
            );
            return;
          }
        }

        setIssue(null);
        setStatusMessage(null);
      } catch {
        if (!mountedRef.current || operation !== operationRef.current) return;
        try {
          const readback = await readWindowAlwaysOnTop();
          if (!mountedRef.current || operation !== operationRef.current) return;
          setActual(toActual(readback));
        } catch {
          setActual("unknown");
        }
        setIssue("window");
        needsApplyOnFocusRef.current = true;
        setStatusMessage("前面固定を変更できませんでした");
      } finally {
        if (mountedRef.current && operation === operationRef.current)
          setBusy(false);
      }
    },
    [storageKey],
  );

  useEffect(() => {
    void applyDesired(initialDesiredRef.current);
  }, [applyDesired]);

  useEffect(() => {
    let disposed = false;
    let unlisten: () => void = () => undefined;
    void onCurrentWindowFocused(() => {
      if (needsApplyOnFocusRef.current) {
        void applyDesired(desiredRef.current);
      }
    })
      .then((release) => {
        if (disposed) release();
        else unlisten = release;
      })
      .catch((error: unknown) => {
        console.warn("[useAlwaysOnTop] focus listener unavailable", error);
      });
    return () => {
      disposed = true;
      unlisten();
    };
  }, [applyDesired]);

  const toggle = useCallback(async () => {
    if (busy || actual === "unknown") return;
    await applyDesired(actual !== "off" ? false : true);
  }, [actual, applyDesired, busy]);

  const retry = useCallback(async () => {
    await applyDesired(desiredRef.current);
  }, [applyDesired]);

  return { actual, busy, issue, statusMessage, toggle, retry };
}
