import { $, browser, expect } from "@wdio/globals";
import {
  localBackendRequest,
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
  vad_engine: "silero" | "webrtc";
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
      if (!settingsSnapshot) throw new Error("STT settings snapshot is missing");
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

  it("shows managed pricing", async () => {
    await openSettings(waitOptions);
    const managedCard = await $('[data-route-id="managed"]');
    await managedCard.waitForDisplayed(waitOptions);
    expect(await managedCard.getText()).toContain(
      "提供時に料金をご案内（無料ではありません）",
    );
    await closeSettingsIfOpen({ discard: true, waitOptions });
  });

  it("shows and toggles three AI use cases independently", async () => {
    await openSettings(waitOptions);

    const codexCard = await $('[data-route-id="codex"]');
    await codexCard.waitForDisplayed(waitOptions);
    for (const label of ["返答案", "会話メモ", "要約・議事録"]) {
      await expect(
        codexCard.$(`.//button[normalize-space()="${label}"]`),
      ).toBeDisplayed();
    }

    const geminiCard = await $('[data-route-id="gemini"]');
    const reply = await geminiCard.$('.//button[normalize-space()="返答案"]');
    const info = await geminiCard.$('.//button[normalize-space()="会話メモ"]');
    const minutes = await geminiCard.$(
      './/button[normalize-space()="要約・議事録"]',
    );
    await reply.waitForClickable(waitOptions);
    const initialReply = await reply.getAttribute("aria-pressed");
    const initialInfo = await info.getAttribute("aria-pressed");
    const initialMinutes = await minutes.getAttribute("aria-pressed");

    await reply.click();

    expect(await reply.getAttribute("aria-pressed")).toBe(
      initialReply === "true" ? "false" : "true",
    );
    expect(await info.getAttribute("aria-pressed")).toBe(initialInfo);
    expect(await minutes.getAttribute("aria-pressed")).toBe(initialMinutes);
    await closeSettingsIfOpen({ discard: true, waitOptions });
  });

  it("discards an unsaved Gemini credential draft", async () => {
    await openSettings(waitOptions);
    const input = await geminiCredentialInput();
    await input.setValue("gemini-inline-unsaved-draft");
    expect(await input.getValue()).toBe("gemini-inline-unsaved-draft");
    await closeSettingsIfOpen({ discard: true, waitOptions });

    await openSettings(waitOptions);
    const reopenedInput = await $('input[aria-label="Google Gemini APIキー"]');
    if (await reopenedInput.isExisting()) {
      expect(await reopenedInput.getValue()).toBe("");
    } else {
      await expect(
        $('button[aria-label="Google Gemini APIキーを変更"]'),
      ).toBeDisplayed();
    }
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

  it("offers Torch-free Silero controls when selected", async () => {
    await persistSttPatch({ vad_engine: "silero" });
    await browser.refresh();
    await waitForBackendReady();
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
    await openSettings(waitOptions);
    const audioCategory = await $(
      '//button[.//span[normalize-space()="音声"]]',
    );
    await audioCategory.waitForClickable(waitOptions);
    await audioCategory.click();

    const vadEngine = await $('select[aria-label="声の検出方法"]');
    await vadEngine.waitForDisplayed(waitOptions);
    expect(await vadEngine.getValue()).toBe("silero");
    const sileroOption = await $(
      'select[aria-label="声の検出方法"] option[value="silero"]',
    );
    expect(await sileroOption.getText()).toBe("Silero VAD（高精度・おすすめ）");
    await expect(
      $('input[aria-label="Silero音声判定しきい値"]'),
    ).toBeDisplayed();

    await closeSettingsIfOpen({ discard: true, waitOptions });
  });

  it("locks audio settings while a meeting is active", async () => {
    await persistSttPatch({ backend: "dummy" });
    await browser.refresh();
    await waitForBackendReady();
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
    await startMeeting(waitOptions);
    await openSettings(waitOptions);
    const audioCategory = await $(
      '//button[.//span[normalize-space()="音声"]]',
    );
    await audioCategory.waitForClickable(waitOptions);
    await audioCategory.click();

    const lockNotice = await $(
      '//*[contains(normalize-space(.), "会議中は音声認識の設定を変更できません")]',
    );
    await lockNotice.waitForDisplayed(waitOptions);
    expect(
      await $('select[aria-label="音声認識方式"]').isEnabled(),
    ).toBe(false);
    expect(
      await $('select[aria-label="声の検出方法"]').isEnabled(),
    ).toBe(false);

    await closeSettingsIfOpen({ discard: true, waitOptions });
  });
});
