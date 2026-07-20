import { $, browser } from "@wdio/globals";
import type { WaitOptions } from "./backend";
import { expectDisplayedSurface } from "./displayedSurface";

export async function prepareAudioIfNeeded(
  waitOptions: WaitOptions,
): Promise<void> {
  const prepareButton = await $('//button[normalize-space()="音声を準備する"]');
  if (!(await prepareButton.isExisting())) return;
  await prepareButton.waitForClickable(waitOptions);
  await prepareButton.click();
  await $('//*[normalize-space()="音声の準備ができました"]').waitForExist(
    waitOptions,
  );
}

export async function startMeeting(waitOptions: WaitOptions): Promise<void> {
  await browser.tauri.switchWindow("main");
  await prepareAudioIfNeeded(waitOptions);
  const start = await $('//button[normalize-space()="会議を開始"]');
  await start.waitForEnabled(waitOptions);
  await start.click();
  await expectDisplayedSurface(
    '[data-testid="meeting-control-screen"]',
    waitOptions,
  );
}

export async function finishMeeting(waitOptions: WaitOptions): Promise<void> {
  await browser.tauri.switchWindow("main");
  const endButton = await $('button[aria-label="会議を終了"]');
  const setupScreen = await $('[data-testid="setup-screen"]');
  await browser.waitUntil(
    async () =>
      (await endButton.isDisplayed()) || (await setupScreen.isDisplayed()),
    {
      ...waitOptions,
      timeoutMsg: "Neither the active meeting nor setup surface became ready",
    },
  );
  if (!(await endButton.isDisplayed())) return;
  await endButton.waitForClickable(waitOptions);
  await endButton.click();
  const confirm = await $('//button[normalize-space()="終了する"]');
  await confirm.waitForDisplayed(waitOptions);
  await confirm.click();
  await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
}

export async function waitForAssistantWindow(
  waitOptions: WaitOptions,
): Promise<void> {
  await browser.waitUntil(
    async () => (await browser.tauri.listWindows()).includes("assistant"),
    { ...waitOptions, timeoutMsg: "Assistant window did not open" },
  );
  await browser.tauri.switchWindow("assistant");
  await expectDisplayedSurface('[data-testid="live-reply-panel"]', waitOptions);
}

export async function hideAssistantWindow(
  waitOptions: WaitOptions,
): Promise<void> {
  await browser.tauri.switchWindow("main");
  const windows = await browser.tauri.listWindows();
  if (!windows.includes("assistant")) return;
  await browser.tauri.execute(({ core }) =>
    core.invoke("set_assistant_window_visible", { visible: false }),
  );
  await browser.waitUntil(
    async () =>
      !(await browser.tauri.execute(({ core }) =>
        core.invoke("plugin:window|is_visible", { label: "assistant" }),
      )),
    { ...waitOptions, timeoutMsg: "Assistant window remained visible" },
  );
}
