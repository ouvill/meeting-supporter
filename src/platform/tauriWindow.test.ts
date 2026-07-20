import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getCurrentAppWindowLabel,
  onCurrentWindowFocused,
  readWindowAlwaysOnTop,
  setAssistantWindowVisible,
  setWindowAlwaysOnTop,
} from "./tauriWindow";

const isTauriMock = vi.hoisted(() => vi.fn<() => boolean>());
const invokeMock = vi.hoisted(() =>
  vi.fn<(_command: string) => Promise<void>>(async () => {}),
);
const currentWindowLabel = vi.hoisted(() => ({ value: "main" }));
const alwaysOnTopState = vi.hoisted(() => ({ value: false }));
const getCurrentWindowMock = vi.hoisted(() => vi.fn<() => MockCurrentWindow>());
const focusHandler = vi.hoisted(
  () =>
    ({ value: null }) as {
      value: ((event: { payload: boolean }) => void) | null;
    },
);
const unlistenMock = vi.hoisted(() => vi.fn());

interface MockCurrentWindow {
  label: string;
  hide: () => Promise<void>;
  isAlwaysOnTop: () => Promise<boolean>;
  setAlwaysOnTop: (alwaysOnTop: boolean) => Promise<void>;
  onFocusChanged: (
    handler: (event: { payload: boolean }) => void,
  ) => Promise<() => void>;
}

vi.mock("@tauri-apps/api/core", () => ({
  isTauri: isTauriMock,
  invoke: invokeMock,
}));

vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: getCurrentWindowMock,
}));

describe("tauriWindow", () => {
  beforeEach(() => {
    isTauriMock.mockReturnValue(false);
    invokeMock.mockClear();
    getCurrentWindowMock.mockClear();
    currentWindowLabel.value = "main";
    getCurrentWindowMock.mockImplementation(() => ({
      label: currentWindowLabel.value,
      onFocusChanged: async (handler) => {
        focusHandler.value = handler;
        return unlistenMock;
      },
      hide: async () => {},
      isAlwaysOnTop: async () => alwaysOnTopState.value,
      setAlwaysOnTop: async (alwaysOnTop: boolean) => {
        alwaysOnTopState.value = alwaysOnTop;
      },
    }));
    alwaysOnTopState.value = false;
    focusHandler.value = null;
    unlistenMock.mockReset();
  });

  it("ブラウザ環境では main window として扱い、Tauri API を呼ばない", () => {
    expect(getCurrentAppWindowLabel()).toBe("main");
    expect(getCurrentWindowMock).not.toHaveBeenCalled();
  });

  it("Tauri 環境では current window label から assistant を判定する", () => {
    isTauriMock.mockReturnValue(true);
    currentWindowLabel.value = "assistant";

    expect(getCurrentAppWindowLabel()).toBe("assistant");
  });

  it("Tauri 環境では Rust command 経由で assistant window を表示できる", async () => {
    isTauriMock.mockReturnValue(true);

    await setAssistantWindowVisible(true);

    expect(invokeMock).toHaveBeenCalledWith("set_assistant_window_visible", {
      visible: true,
    });
  });

  it("ブラウザ環境では assistant window 表示更新を行わない", async () => {
    await setAssistantWindowVisible(true);

    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("forwards only focused window events and returns the native unlisten", async () => {
    isTauriMock.mockReturnValue(true);
    const listener = vi.fn();

    const unlisten = await onCurrentWindowFocused(listener);
    focusHandler.value?.({ payload: false });
    expect(listener).not.toHaveBeenCalled();

    focusHandler.value?.({ payload: true });
    expect(listener).toHaveBeenCalledOnce();

    unlisten();
    expect(unlistenMock).toHaveBeenCalledOnce();
  });

  it("Tauri の actual always-on-top state を設定して読み戻す", async () => {
    isTauriMock.mockReturnValue(true);

    await setWindowAlwaysOnTop(true);

    expect(await readWindowAlwaysOnTop()).toBe(true);
    expect(getCurrentWindowMock).toHaveBeenCalled();
  });

  it("ブラウザ環境では actual state を推測しない", async () => {
    await setWindowAlwaysOnTop(true);

    expect(await readWindowAlwaysOnTop()).toBeNull();
    expect(getCurrentWindowMock).not.toHaveBeenCalled();
  });
});
