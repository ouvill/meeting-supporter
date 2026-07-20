import { $, browser } from "@wdio/globals";
import { expectDisplayedSurface } from "./displayedSurface";
import type { WaitOptions } from "./backend";

export async function openSettings(waitOptions: WaitOptions): Promise<void> {
  const modal = await $('[data-testid="settings-modal"]');
  if (!(await modal.isExisting())) {
    const settingsButton = await $('button[aria-label="設定"]');
    await settingsButton.waitForClickable(waitOptions);
    await settingsButton.click();
  }
  await expectDisplayedSurface('[data-testid="settings-modal"]', waitOptions);
  await expectDisplayedSurface(
    'section[data-settings-page="支援方法"]',
    waitOptions,
  );
}

export async function closeSettingsIfOpen({
  discard,
  waitOptions,
}: {
  discard: boolean;
  waitOptions: WaitOptions;
}): Promise<void> {
  await browser.tauri.switchWindow("main");
  const modal = await $('[data-testid="settings-modal"]');
  if (!(await modal.isExisting())) return;

  const close = await $('button[aria-label="設定を閉じる"]');
  await close.waitForClickable(waitOptions);
  await close.click();

  const discardAction = await $(
    '//button[normalize-space()="変更を破棄して閉じる"]',
  );
  if (await discardAction.isExisting()) {
    const action = discard
      ? discardAction
      : await $('//button[normalize-space()="設定に戻る"]');
    await action.waitForClickable(waitOptions);
    await action.click();
    if (!discard) return;
  }

  await modal.waitForExist({ ...waitOptions, reverse: true });
}
