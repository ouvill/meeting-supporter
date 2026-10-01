import { $, browser, expect } from "@wdio/globals";
import { localBackendRequest, waitForBackendReady } from "./helpers/backend";
import { expectDisplayedSurface } from "./helpers/displayedSurface";
import {
  finishMeeting,
  hideAssistantWindow,
  startMeeting,
} from "./helpers/meetingLifecycle";

const waitOptions = { timeout: 20_000, interval: 200 };

describe("Rust desktop meeting persistence", () => {
  before(async () => {
    await waitForBackendReady();
    expect(await localBackendRequest({ path: "/health" })).toEqual({
      status: "ok",
      runtime: "rust",
    });
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
  });

  afterEach(async () => {
    const back = await $('button[aria-label="会議履歴を閉じて戻る"]');
    if (await back.isExisting()) await back.click();
    await finishMeeting(waitOptions);
    await hideAssistantWindow(waitOptions);
  });

  it("reconnects an active meeting, saves both inputs, and deletes history through the UI", async () => {
    await startMeeting(waitOptions);
    await browser.refresh();
    await waitForBackendReady();
    await expectDisplayedSurface(
      '[data-testid="meeting-control-screen"]',
      waitOptions,
    );
    await finishMeeting(waitOptions);
    await hideAssistantWindow(waitOptions);

    const history = await $('//button[normalize-space()="過去の会議"]');
    await history.waitForClickable(waitOptions);
    await history.click();
    await expectDisplayedSurface(
      '[data-testid="meeting-history-screen"]',
      waitOptions,
    );
    // The synthetic recognizer emits each input's final utterance during drain.
    await $('//*[normalize-space()="synthetic 0。"]').waitForDisplayed(
      waitOptions,
    );
    await $('//*[normalize-space()="synthetic 1。"]').waitForDisplayed(
      waitOptions,
    );
    await $('button[aria-label="タイトルを編集"]').click();
    await $('input[aria-label="会議タイトル"]').setValue(
      "Synthetic Rust meeting",
    );
    await $('button[aria-label="タイトルを保存"]').click();
    await browser.refresh();
    await waitForBackendReady();
    // Renderer navigation resets, while the saved meeting remains in SQLite.
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
    await browser.tauri.execute(() => {
      window.addEventListener("securitypolicyviolation", (event) => {
        document.body.dataset.e2eBlockedDirective = event.effectiveDirective;
      });
    });
    await $('//button[normalize-space()="過去の会議"]').click();
    await $('//*[normalize-space()="Synthetic Rust meeting"]').waitForDisplayed(
      waitOptions,
    );
    await browser
      .waitUntil(
        async () =>
          browser.tauri.execute(() => {
            const recordings = [...document.querySelectorAll("audio")];
            return (
              recordings.length === 2 &&
              recordings.every(
                (audio) => audio.readyState >= 1 && audio.duration > 0,
              )
            );
          }),
        {
          ...waitOptions,
          timeoutMsg: "Both saved WAV recordings did not load",
        },
      )
      .catch(async () => {
        const status = await browser.tauri.execute(() => ({
          blockedDirective: document.body.dataset.e2eBlockedDirective ?? null,
          audio: [...document.querySelectorAll("audio")].map((audio) => ({
            readyState: audio.readyState,
            errorCode: audio.error?.code ?? null,
            hasBlob: audio.currentSrc.startsWith("blob:"),
            duration: Number.isFinite(audio.duration) ? audio.duration : null,
          })),
        }));
        throw new Error(
          `Saved recording metadata unavailable: ${JSON.stringify(status)}`,
        );
      });
    await $('button[aria-label="削除"]').click();
    const confirm = await $('//button[normalize-space()="削除する"]');
    await confirm.waitForClickable(waitOptions);
    await confirm.click();
    await $('//*[normalize-space()="会議履歴がありません"]').waitForDisplayed(
      waitOptions,
    );
    expect(await localBackendRequest({ path: "/meetings" })).toMatchObject({
      items: [],
    });
  });
});
