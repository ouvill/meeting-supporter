import { $, browser, expect } from "@wdio/globals";
import { waitForBackendReady } from "./helpers/backend";
import { expectDisplayedSurface } from "./helpers/displayedSurface";
import { finishMeeting, hideAssistantWindow } from "./helpers/meetingLifecycle";
import { closeSettingsIfOpen } from "./helpers/settings";

const waitOptions = { timeout: 15_000, interval: 200 };
const assistantPinStorageKey = "meeting-supporter.assistant-always-on-top";
type AppWindowLabel = "main" | "assistant";

interface WindowSnapshot {
  mainNative: boolean;
  mainPressed: boolean;
  assistantNative: boolean;
  assistantPressed: boolean;
  assistantStorage: string | null;
}

let snapshot: WindowSnapshot | null = null;

async function setAssistantVisible(visible: boolean): Promise<void> {
  await browser.tauri.switchWindow("main");
  await browser.tauri.execute(
    ({ core }, nextVisible) =>
      core.invoke("set_assistant_window_visible", { visible: nextVisible }),
    visible,
  );
}

async function currentWindowAlwaysOnTop(
  label: AppWindowLabel,
): Promise<boolean> {
  return browser.tauri.execute(
    ({ core }, currentLabel) =>
      core.invoke("plugin:window|is_always_on_top", { label: currentLabel }),
    label,
  ) as Promise<boolean>;
}

async function assistantWindowVisible(): Promise<boolean> {
  await browser.tauri.switchWindow("main");
  return browser.tauri.execute(({ core }) =>
    core.invoke("plugin:window|is_visible", { label: "assistant" }),
  ) as Promise<boolean>;
}

async function pressedState(): Promise<boolean> {
  const button = await $('button[title^="常に前面"]');
  await button.waitForDisplayed(waitOptions);
  await browser.waitUntil(
    async () =>
      ["true", "false"].includes(
        (await button.getAttribute("aria-pressed")) ?? "",
      ),
    { ...waitOptions, timeoutMsg: "Window pin state remained unknown" },
  );
  return (await button.getAttribute("aria-pressed")) === "true";
}

async function togglePin(
  label: AppWindowLabel,
  expected: boolean,
): Promise<void> {
  const button = await $('button[title^="常に前面"]');
  await button.click();
  await browser.waitUntil(
    async () =>
      (await pressedState()) === expected &&
      (await currentWindowAlwaysOnTop(label)) === expected,
    {
      ...waitOptions,
      timeoutMsg: `Window pin state did not become ${expected ? "on" : "off"}`,
    },
  );
}

async function showAssistant(): Promise<void> {
  await setAssistantVisible(true);
  await browser.waitUntil(
    async () => (await browser.tauri.listWindows()).includes("assistant"),
    { ...waitOptions, timeoutMsg: "Assistant window was not available" },
  );
  await browser.tauri.switchWindow("assistant");
  await expectDisplayedSurface('[data-testid="live-reply-panel"]', waitOptions);
}

async function restoreWindows(): Promise<void> {
  const cleanupErrors: unknown[] = [];
  if (snapshot) {
    try {
      await showAssistant();
      await browser.tauri.execute(
        (_tauri, key, value) => {
          if (value === null) window.localStorage.removeItem(key);
          else window.localStorage.setItem(key, value);
        },
        assistantPinStorageKey,
        snapshot.assistantStorage,
      );
      await browser.refresh();
      await expectDisplayedSurface(
        '[data-testid="live-reply-panel"]',
        waitOptions,
      );
      if (
        process.platform !== "linux" &&
        (await pressedState()) !== snapshot.assistantPressed
      ) {
        await togglePin("assistant", snapshot.assistantPressed);
      }
      expect(await currentWindowAlwaysOnTop("assistant")).toBe(
        snapshot.assistantNative,
      );
    } catch (error) {
      cleanupErrors.push(error);
    }
    try {
      await browser.tauri.switchWindow("main");
      if (
        process.platform !== "linux" &&
        (await pressedState()) !== snapshot.mainPressed
      ) {
        await togglePin("main", snapshot.mainPressed);
      }
      expect(await currentWindowAlwaysOnTop("main")).toBe(snapshot.mainNative);
    } catch (error) {
      cleanupErrors.push(error);
    }
  }
  try {
    await hideAssistantWindow(waitOptions);
  } catch (error) {
    cleanupErrors.push(error);
  }
  if (cleanupErrors.length > 0) {
    throw new AggregateError(
      cleanupErrors,
      "Window controls E2E cleanup failed",
    );
  }
}

describe("Native window controls", () => {
  beforeEach(async () => {
    await browser.tauri.switchWindow("main");
    await waitForBackendReady();
    await closeSettingsIfOpen({ discard: true, waitOptions });
    await finishMeeting(waitOptions);
    await hideAssistantWindow(waitOptions);
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
    const mainPressed = await pressedState();
    const mainNative = await currentWindowAlwaysOnTop("main");

    await showAssistant();
    const assistantPressed = await pressedState();
    const assistantNative = await currentWindowAlwaysOnTop("assistant");
    const assistantStorage = await browser.tauri.execute(
      (_tauri, key) => window.localStorage.getItem(key),
      assistantPinStorageKey,
    );
    snapshot = {
      mainNative,
      mainPressed,
      assistantNative,
      assistantPressed,
      assistantStorage,
    };
  });

  afterEach(async () => {
    await restoreWindows();
    snapshot = null;
  });

  it("matches labels, titles, surfaces, and UI/native pin state", async () => {
    expect(await browser.tauri.listWindows()).toEqual(
      expect.arrayContaining(["main", "assistant"]),
    );
    await browser.tauri.switchWindow("main");
    expect(await browser.getTitle()).toBe("会議支援AI");
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
    expect(await pressedState()).toBe(await currentWindowAlwaysOnTop("main"));

    await browser.tauri.switchWindow("assistant");
    expect(await browser.getTitle()).toBe("ライブ返答支援");
    await expectDisplayedSurface(
      '[data-testid="live-reply-panel"]',
      waitOptions,
    );
    expect(await pressedState()).toBe(
      await currentWindowAlwaysOnTop("assistant"),
    );
  });

  it("keeps the main pin unchanged when the assistant pin changes", async () => {
    if (!snapshot) throw new Error("Window snapshot is missing");
    const target =
      process.platform === "linux"
        ? snapshot.assistantPressed
        : !snapshot.assistantPressed;
    await browser.tauri.switchWindow("assistant");
    if (target !== snapshot.assistantPressed) {
      await togglePin("assistant", target);
    } else {
      await setAssistantVisible(false);
      await showAssistant();
    }
    expect(await pressedState()).toBe(target);
    expect(await currentWindowAlwaysOnTop("assistant")).toBe(target);

    await browser.tauri.switchWindow("main");
    expect(await pressedState()).toBe(snapshot.mainPressed);
    expect(await currentWindowAlwaysOnTop("main")).toBe(snapshot.mainNative);
  });

  it("persists assistant pin storage across native hide, reuse, and renderer reload", async () => {
    if (!snapshot) throw new Error("Window snapshot is missing");
    const target =
      process.platform === "linux"
        ? snapshot.assistantPressed
        : !snapshot.assistantPressed;
    await browser.tauri.switchWindow("assistant");
    if (target !== snapshot.assistantPressed) {
      await togglePin("assistant", target);
    }
    expect(
      await browser.tauri.execute(
        (_tauri, key) => window.localStorage.getItem(key),
        assistantPinStorageKey,
      ),
    ).toBe(
      snapshot.assistantStorage === null && process.platform === "linux"
        ? snapshot.assistantStorage
        : String(target),
    );

    await browser.tauri.execute(({ core }) =>
      core.invoke("plugin:window|close", { label: "assistant" }),
    );
    await browser.tauri.switchWindow("main");
    await browser.waitUntil(async () => !(await assistantWindowVisible()), {
      ...waitOptions,
      timeoutMsg: "Native assistant close did not hide the reusable window",
    });
    expect(await browser.tauri.listWindows()).toContain("assistant");

    await showAssistant();
    expect(await pressedState()).toBe(target);
    expect(await currentWindowAlwaysOnTop("assistant")).toBe(target);
    await browser.refresh();
    await expectDisplayedSurface(
      '[data-testid="live-reply-panel"]',
      waitOptions,
    );
    expect(await pressedState()).toBe(target);
    expect(await currentWindowAlwaysOnTop("assistant")).toBe(target);
  });
});
