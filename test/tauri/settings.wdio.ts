import { $, browser, expect } from "@wdio/globals";
import { waitForBackendReady } from "./helpers/backend";
import { expectDisplayedSurface } from "./helpers/displayedSurface";
import { finishMeeting, hideAssistantWindow } from "./helpers/meetingLifecycle";
import { closeSettingsIfOpen, openSettings } from "./helpers/settings";

const waitOptions = { timeout: 20_000, interval: 200 };
let originalWindowSize: { width: number; height: number } | null = null;

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
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
  });

  afterEach(async () => {
    await cleanupState();
    originalWindowSize = null;
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

  it("offers ReazonSpeech as a Japanese local speech model", async () => {
    await openSettings(waitOptions);
    const audioCategory = await $(
      '//button[.//span[normalize-space()="音声"]]',
    );
    await audioCategory.waitForClickable(waitOptions);
    await audioCategory.click();

    const backend = await $('select[aria-label="音声認識方式"]');
    await backend.waitForDisplayed(waitOptions);
    await backend.waitForEnabled(waitOptions);
    const backendOptions = (await browser.tauri.execute(() => {
      const select = document.querySelector<HTMLSelectElement>(
        'select[aria-label="音声認識方式"]',
      );
      return select ? [...select.options].map((option) => option.value) : [];
    })) as string[];
    expect(backendOptions).toContain("reazonspeech");
    await browser.tauri.execute(() => {
      const select = document.querySelector<HTMLSelectElement>(
        'select[aria-label="音声認識方式"]',
      );
      if (!select) throw new Error("Speech recognition selector is missing");
      select.value = "reazonspeech";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await browser.waitUntil(
      async () => (await backend.getValue()) === "reazonspeech",
      waitOptions,
    );

    const modelCardTitle = await $(
      '//h4[normalize-space()="ReazonSpeech日本語モデル"]',
    );
    await modelCardTitle.waitForExist(waitOptions);
    await modelCardTitle.scrollIntoView();
    await expect(modelCardTitle).toBeDisplayed();
    await expect(
      $('//*[normalize-space()="日本語・約153 MB"]'),
    ).toBeDisplayed();
    const language = await $('select[aria-label="会議の言語"]');
    expect(await language.isEnabled()).toBe(false);

    await closeSettingsIfOpen({ discard: true, waitOptions });
  });
});
