import { StrictMode, type ReactNode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ASSISTANT_ALWAYS_ON_TOP_KEY, useAlwaysOnTop } from "./useAlwaysOnTop";

const setAlwaysOnTopMock = vi.hoisted(() =>
  vi.fn<(desired: boolean) => Promise<void>>(),
);
const readAlwaysOnTopMock = vi.hoisted(() =>
  vi.fn<() => Promise<boolean | null>>(),
);
const focusListenerMock = vi.hoisted(() =>
  vi.fn<(listener: () => void) => Promise<() => void>>(),
);

vi.mock("../platform/tauriWindow", () => ({
  setWindowAlwaysOnTop: setAlwaysOnTopMock,
  readWindowAlwaysOnTop: readAlwaysOnTopMock,
  onCurrentWindowFocused: focusListenerMock,
}));

function StrictWrapper({ children }: { children: ReactNode }) {
  return <StrictMode>{children}</StrictMode>;
}

function renderAssistantHook() {
  return renderHook(
    () =>
      useAlwaysOnTop({
        defaultDesired: true,
        storageKey: ASSISTANT_ALWAYS_ON_TOP_KEY,
      }),
    { wrapper: StrictWrapper },
  );
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

describe("useAlwaysOnTop", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
    setAlwaysOnTopMock.mockReset().mockResolvedValue(undefined);
    readAlwaysOnTopMock.mockReset().mockResolvedValue(true);
    focusListenerMock.mockReset().mockResolvedValue(() => undefined);
  });

  it.each([
    ["true", true, "on"],
    ["false", false, "off"],
  ] as const)(
    "applies stored %s and exposes only actual readback",
    async (stored, desired, actual) => {
      window.localStorage.setItem(ASSISTANT_ALWAYS_ON_TOP_KEY, stored);
      readAlwaysOnTopMock.mockResolvedValue(desired);

      const { result } = renderAssistantHook();

      await waitFor(() => expect(result.current.busy).toBe(false));
      expect(setAlwaysOnTopMock).toHaveBeenLastCalledWith(desired);
      expect(result.current.actual).toBe(actual);
      expect(result.current.issue).toBeNull();
      expect(window.localStorage.getItem(ASSISTANT_ALWAYS_ON_TOP_KEY)).toBe(
        stored,
      );
    },
  );

  it("uses default ON for an invalid stored value and normalizes it after matching readback", async () => {
    window.localStorage.setItem(ASSISTANT_ALWAYS_ON_TOP_KEY, "invalid");

    const { result } = renderAssistantHook();

    await waitFor(() => expect(result.current.actual).toBe("on"));
    expect(setAlwaysOnTopMock).toHaveBeenLastCalledWith(true);
    expect(window.localStorage.getItem(ASSISTANT_ALWAYS_ON_TOP_KEY)).toBe(
      "true",
    );
  });

  it("keeps actual ON but reports storage failure when localStorage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("read denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("write denied");
    });

    const { result } = renderAssistantHook();

    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(setAlwaysOnTopMock).toHaveBeenLastCalledWith(true);
    expect(result.current.actual).toBe("on");
    expect(result.current.issue).toBe("storage");
    expect(result.current.statusMessage).toContain("次回起動用の保存に失敗");
  });

  it("does not overwrite desired storage when setAlwaysOnTop fails", async () => {
    window.localStorage.setItem(ASSISTANT_ALWAYS_ON_TOP_KEY, "true");
    setAlwaysOnTopMock.mockRejectedValue(new Error("window manager denied"));
    readAlwaysOnTopMock.mockResolvedValue(false);

    const { result } = renderAssistantHook();

    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(result.current.actual).toBe("off");
    expect(result.current.issue).toBe("window");
    expect(window.localStorage.getItem(ASSISTANT_ALWAYS_ON_TOP_KEY)).toBe(
      "true",
    );
  });

  it("reports a persistent readback mismatch and saves nothing until retry succeeds", async () => {
    window.localStorage.setItem(ASSISTANT_ALWAYS_ON_TOP_KEY, "invalid");
    readAlwaysOnTopMock.mockResolvedValue(false);
    const setItem = vi.spyOn(Storage.prototype, "setItem");

    const { result } = renderAssistantHook();

    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(result.current.actual).toBe("off");
    expect(result.current.issue).toBe("window");
    expect(setItem).not.toHaveBeenCalledWith(
      ASSISTANT_ALWAYS_ON_TOP_KEY,
      "true",
    );

    readAlwaysOnTopMock.mockResolvedValue(true);
    await act(() => result.current.retry());

    expect(result.current.actual).toBe("on");
    expect(result.current.issue).toBeNull();
    expect(setItem).toHaveBeenCalledWith(ASSISTANT_ALWAYS_ON_TOP_KEY, "true");
  });

  it("waits for delayed window-manager readback before saving", async () => {
    readAlwaysOnTopMock
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(false)
      .mockResolvedValue(true);

    const { result } = renderAssistantHook();

    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(result.current.actual).toBe("on");
    expect(result.current.issue).toBeNull();
    expect(window.localStorage.getItem(ASSISTANT_ALWAYS_ON_TOP_KEY)).toBe(
      "true",
    );
  });

  it("reapplies the desired state on focus after an initial mismatch", async () => {
    readAlwaysOnTopMock.mockResolvedValue(false);
    const unlisten = vi.fn();
    let focus: (() => void) | null = null;
    focusListenerMock.mockImplementation(async (listener) => {
      focus = listener;
      return unlisten;
    });

    const { result, unmount } = renderAssistantHook();
    await waitFor(() => expect(result.current.issue).toBe("window"));

    readAlwaysOnTopMock.mockResolvedValue(true);
    const setCallsBeforeFocus = setAlwaysOnTopMock.mock.calls.length;
    await act(async () => {
      focus?.();
    });

    await waitFor(() => expect(result.current.issue).toBeNull());
    expect(setAlwaysOnTopMock.mock.calls.length).toBeGreaterThan(
      setCallsBeforeFocus,
    );
    const unlistenCallsBeforeUnmount = unlisten.mock.calls.length;
    unmount();
    expect(unlisten).toHaveBeenCalledTimes(unlistenCallsBeforeUnmount + 1);
  });

  it("toggles from readback state and persists the confirmed new preference", async () => {
    window.localStorage.setItem(ASSISTANT_ALWAYS_ON_TOP_KEY, "true");
    readAlwaysOnTopMock.mockResolvedValueOnce(true).mockResolvedValue(false);
    const { result } = renderAssistantHook();
    await waitFor(() => expect(result.current.actual).toBe("on"));

    await act(() => result.current.toggle());

    expect(setAlwaysOnTopMock).toHaveBeenLastCalledWith(false);
    expect(result.current.actual).toBe("off");
    expect(window.localStorage.getItem(ASSISTANT_ALWAYS_ON_TOP_KEY)).toBe(
      "false",
    );
  });

  it("ignores stale completion after unmount", async () => {
    const setting = deferred<void>();
    setAlwaysOnTopMock.mockReturnValue(setting.promise);
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const { unmount } = renderAssistantHook();

    unmount();
    setting.resolve(undefined);
    await setting.promise;
    await Promise.resolve();

    expect(readAlwaysOnTopMock).not.toHaveBeenCalled();
    expect(setItem).not.toHaveBeenCalled();
  });
});
