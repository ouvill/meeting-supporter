import { invoke, isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import type { Window as TauriWindow } from "@tauri-apps/api/window";

export type AppWindowLabel = "main" | "assistant";

const ASSISTANT_LABEL: AppWindowLabel = "assistant";
const MAIN_LABEL: AppWindowLabel = "main";

function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && isTauri();
}

export function getCurrentAppWindowLabel(): AppWindowLabel {
  if (!isTauriRuntime()) return MAIN_LABEL;

  try {
    const label = getCurrentWindow().label;
    return label === ASSISTANT_LABEL ? ASSISTANT_LABEL : MAIN_LABEL;
  } catch (error) {
    console.warn("[tauriWindow] failed to read current window label", error);
    return MAIN_LABEL;
  }
}
type AlwaysOnTopWindow = Pick<TauriWindow, "isAlwaysOnTop" | "setAlwaysOnTop">;

function resolveAlwaysOnTopWindow(
  windowLike?: AlwaysOnTopWindow,
): AlwaysOnTopWindow | null {
  if (windowLike) return windowLike;
  return isTauriRuntime() ? getCurrentWindow() : null;
}

export async function readWindowAlwaysOnTop(
  windowLike?: AlwaysOnTopWindow,
): Promise<boolean | null> {
  const appWindow = resolveAlwaysOnTopWindow(windowLike);
  return appWindow ? appWindow.isAlwaysOnTop() : null;
}
export async function onCurrentWindowFocused(
  listener: () => void,
): Promise<() => void> {
  if (!isTauriRuntime()) return () => undefined;
  return getCurrentWindow().onFocusChanged(({ payload: focused }) => {
    if (focused) listener();
  });
}

export async function setWindowAlwaysOnTop(
  alwaysOnTop: boolean,
  windowLike?: AlwaysOnTopWindow,
): Promise<void> {
  const appWindow = resolveAlwaysOnTopWindow(windowLike);
  if (appWindow) await appWindow.setAlwaysOnTop(alwaysOnTop);
}

export async function setAssistantWindowVisible(
  visible: boolean,
): Promise<void> {
  if (!isTauriRuntime()) return;

  try {
    await invoke<void>("set_assistant_window_visible", { visible });
  } catch (error) {
    console.warn(
      "[tauriWindow] failed to update assistant window visibility",
      error,
    );
  }
}

export async function hideCurrentWindow(): Promise<void> {
  if (!isTauriRuntime()) return;

  try {
    await getCurrentWindow().hide();
  } catch (error) {
    console.warn("[tauriWindow] failed to hide current window", error);
  }
}
