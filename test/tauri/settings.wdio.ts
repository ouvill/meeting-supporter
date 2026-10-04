import { $, browser, expect } from "@wdio/globals";
import {
  localBackendRequest,
  syntheticSpeechSettings,
  waitForBackendReady,
} from "./helpers/backend";
import { expectDisplayedSurface } from "./helpers/displayedSurface";
import {
  finishMeeting,
  hideAssistantWindow,
  startMeeting,
} from "./helpers/meetingLifecycle";
import { closeSettingsIfOpen, openSettings } from "./helpers/settings";

type SttSnapshot = Record<string, unknown> & {
  backend: string;
  vad_engine: "silero";
};

interface SettingsSnapshot {
  stt: SttSnapshot;
}

interface SparseSettingsSnapshot {
  stt: Record<string, unknown>;
}

const effectiveSttDefaults = {
  backend: "whisper",
  vad_engine: "silero",
} as const;

function withEffectiveSttValues(
  settings: SparseSettingsSnapshot,
): SettingsSnapshot {
  return {
    ...settings,
    stt: {
      ...effectiveSttDefaults,
      ...settings.stt,
    },
  };
}

const waitOptions = { timeout: 20_000, interval: 200 };
let originalWindowSize: { width: number; height: number } | null = null;
let settingsSnapshot: SettingsSnapshot | null = null;
let sttSettingsMutated = false;

async function geminiCredentialInput() {
  const selector = await $('select[aria-label="返答案に使うAI"]');
  await selector.selectByAttribute("value", "gemini");
  const connection = await $('button[aria-controls="selected-ai-connection"]');
  if ((await connection.getAttribute("aria-expanded")) !== "true")
    await connection.click();
  const geminiCard = await $('[data-route-id="gemini"]');
  await geminiCard.waitForDisplayed(waitOptions);
  let input = await $('input[aria-label="Google Gemini APIキー"]');
  if (!(await input.isExisting())) {
    const edit = await $('button[aria-label="Google Gemini APIキーを変更"]');
    await edit.waitForClickable(waitOptions);
    await edit.click();
    input = await $('input[aria-label="Google Gemini APIキー"]');
    await input.waitForDisplayed(waitOptions);
  }
  return input;
}

async function persistSttPatch(stt: Record<string, unknown>): Promise<void> {
  if (!settingsSnapshot) throw new Error("STT settings snapshot is missing");
  sttSettingsMutated = true;
  await localBackendRequest({
    path: "/api/settings",
    method: "POST",
    body: { stt },
  });
}

async function cleanupState(): Promise<void> {
  const cleanupErrors: unknown[] = [];
  for (const cleanup of [
    () => closeSettingsIfOpen({ discard: true, waitOptions }),
    () => finishMeeting(waitOptions),
    () => hideAssistantWindow(waitOptions),
    async () => {
      if (originalWindowSize) {
        await browser.setWindowSize(
          originalWindowSize.width,
          originalWindowSize.height,
        );
      }
    },
    async () => {
      if (!sttSettingsMutated) return;
      if (!settingsSnapshot)
        throw new Error("STT settings snapshot is missing");
      await localBackendRequest({
        path: "/api/settings",
        method: "POST",
        body: { stt: settingsSnapshot.stt },
      });
    },
    async () => {
      await browser.tauri.switchWindow("main");
      await browser.refresh();
      await waitForBackendReady();
      await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
    },
  ]) {
    try {
      await cleanup();
    } catch (error) {
      cleanupErrors.push(error);
    }
  }
  if (cleanupErrors.length > 0) {
    throw new AggregateError(cleanupErrors, "Settings E2E cleanup failed");
  }
}

describe("Contextual settings credentials", () => {
  beforeEach(async () => {
    await browser.tauri.switchWindow("main");
    await waitForBackendReady();
    await closeSettingsIfOpen({ discard: true, waitOptions });
    await finishMeeting(waitOptions);
    await hideAssistantWindow(waitOptions);
    originalWindowSize = await browser.getWindowSize();
    const sparseSettingsSnapshot =
      await localBackendRequest<SparseSettingsSnapshot>({
        path: "/api/settings",
      });
    settingsSnapshot = withEffectiveSttValues(sparseSettingsSnapshot);
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
  });

  afterEach(async () => {
    try {
      await cleanupState();
    } finally {
      originalWindowSize = null;
      settingsSnapshot = null;
      sttSettingsMutated = false;
    }
  });

  it("omits the unavailable hosted route", async () => {
    await openSettings(waitOptions);
    expect(
      await $(
        'select[aria-label="返答案に使うAI"] option[value="managed"]',
      ).isExisting(),
    ).toBe(false);
    await closeSettingsIfOpen({ discard: true, waitOptions });
  });

  it("selects a reply AI and preserves it when switching settings tabs", async () => {
    await openSettings(waitOptions);
    const selector = await $('select[aria-label="返答案に使うAI"]');
    await selector.selectByAttribute("value", "gemini");
    await $("#ai-settings-tab-speech").click();
    await expect($("#ai-settings-panel-speech")).toBeDisplayed();
    await expect($("#ai-settings-panel-reply")).not.toBeDisplayed();
    await $("#ai-settings-tab-reply").click();
    expect(await selector.getValue()).toBe("gemini");
    await closeSettingsIfOpen({ discard: true, waitOptions });
  });

  it("discards an unsaved Gemini credential draft", async () => {
    await openSettings(waitOptions);
    const input = await geminiCredentialInput();
    await input.setValue("gemini-inline-unsaved-draft");
    expect(await input.getValue()).toBe("gemini-inline-unsaved-draft");
    await closeSettingsIfOpen({ discard: true, waitOptions });

    await openSettings(waitOptions);
    const reopenedInput = await geminiCredentialInput();
    expect(await reopenedInput.getValue()).toBe("");
    await closeSettingsIfOpen({ discard: true, waitOptions });
  });

  it("keeps the discard action visible and clickable at 320px height", async () => {
    await openSettings(waitOptions);
    const input = await geminiCredentialInput();
    await input.setValue("gemini-inline-unsaved-draft");
    if (!originalWindowSize) throw new Error("Window size snapshot is missing");

    await browser.setWindowSize(originalWindowSize.width, 320);
    try {
      await $('button[aria-label="設定を閉じる"]').click();
      const discard = await $(
        '//button[normalize-space()="変更を破棄して閉じる"]',
      );
      await discard.waitForDisplayed(waitOptions);
      const bounds = (await browser.tauri.execute(() => {
        const button = [...document.querySelectorAll("button")].find(
          (candidate) =>
            candidate.textContent?.trim() === "変更を破棄して閉じる",
        );
        if (!button) return null;
        const rect = button.getBoundingClientRect();
        return {
          top: rect.top,
          bottom: rect.bottom,
          viewportHeight: window.innerHeight,
        };
      })) as {
        top: number;
        bottom: number;
        viewportHeight: number;
      } | null;
      if (!bounds) throw new Error("Discard action was not rendered");
      expect(bounds.top).toBeGreaterThanOrEqual(0);
      expect(bounds.bottom).toBeLessThanOrEqual(bounds.viewportHeight);
      await discard.waitForClickable(waitOptions);
      await discard.click();
      await $('[data-testid="settings-modal"]').waitForExist({
        ...waitOptions,
        reverse: true,
      });
    } finally {
      await browser.setWindowSize(
        originalWindowSize.width,
        originalWindowSize.height,
      );
    }
  });

  it("offers Torch-free Silero controls without the retired VAD selector", async () => {
    await persistSttPatch({ vad_engine: "silero" });
    await browser.refresh();
    await waitForBackendReady();
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
    await openSettings(waitOptions);
    const audioCategory = await $("#ai-settings-tab-speech");
    await audioCategory.waitForClickable(waitOptions);
    await audioCategory.click();

    await $('//summary[normalize-space()="音声認識の詳細な調整"]').click();
    expect(await $('option[value="webrtc"]').isExisting()).toBe(false);
    await expect(
      $('input[aria-label="Silero音声判定しきい値"]'),
    ).toBeDisplayed();

    await closeSettingsIfOpen({ discard: true, waitOptions });
  });

  it("locks audio settings while a meeting is active", async () => {
    await persistSttPatch(await syntheticSpeechSettings());
    await browser.refresh();
    await waitForBackendReady();
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
    await startMeeting(waitOptions);
    await openSettings(waitOptions);
    const audioCategory = await $("#ai-settings-tab-speech");
    await audioCategory.waitForClickable(waitOptions);
    await audioCategory.click();

    const lockNotice = await $(
      '//*[contains(normalize-space(.), "会議中は音声認識の設定を変更できません")]',
    );
    await lockNotice.waitForDisplayed(waitOptions);
    expect(await $('select[aria-label="音声認識方式"]').isEnabled()).toBe(
      false,
    );
    await $('//summary[normalize-space()="音声認識の詳細な調整"]').click();
    expect(
      await $('input[aria-label="Silero音声判定しきい値"]').isEnabled(),
    ).toBe(false);

    await closeSettingsIfOpen({ discard: true, waitOptions });
  });
});
