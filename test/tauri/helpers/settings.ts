import { $, browser } from "@wdio/globals";
import { expectDisplayedSurface } from "./displayedSurface";
import type { WaitOptions } from "./backend";

export async function selectReplyAi(
  routeId: string,
  waitOptions: WaitOptions,
): Promise<void> {
  const selector = await $('select[aria-label="返答案に使うAI"]');
  await selector.waitForDisplayed(waitOptions);
  await selector.waitForEnabled(waitOptions);
  // The embedded webdriver 1.2.0 implements option clicks with DOM click(),
  // which does not select the option or emit the select's change event.
  await browser.tauri.execute((_tauri, value) => {
    const select = document.querySelector<HTMLSelectElement>(
      'select[aria-label="返答案に使うAI"]',
    );
    const option =
      select && [...select.options].find((item) => item.value === value);
    if (!select || select.disabled || !option || option.disabled) {
      throw new Error("Reply AI option is unavailable");
    }
    select.value = value;
    select.dispatchEvent(new Event("input", { bubbles: true }));
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }, routeId);
  // Verify React rendered the selected route, not just the DOM value we set.
  await browser.waitUntil(
    () =>
      browser.tauri.execute(
        (_tauri, value) =>
          [...document.querySelectorAll<HTMLElement>("[data-route-id]")].some(
            (element) =>
              element.dataset.routeId === value &&
              element.getClientRects().length > 0,
          ),
        routeId,
      ),
    { ...waitOptions, timeoutMsg: "Selected reply AI did not render" },
  );
}

export async function openSettings(waitOptions: WaitOptions): Promise<void> {
  const modal = await $('[data-testid="settings-modal"]');
  if (!(await modal.isExisting())) {
    const settingsButton = await $('button[aria-label="設定"]');
    await settingsButton.waitForClickable(waitOptions);
    await settingsButton.click();
  }
  await expectDisplayedSurface('[data-testid="settings-modal"]', waitOptions);
  await expectDisplayedSurface(
    'section[data-settings-page="返答案"]',
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
