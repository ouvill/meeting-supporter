import { AxeBuilder } from "@axe-core/webdriverio";
import { $, browser, expect } from "@wdio/globals";
import { localBackendRequest, waitForBackendReady } from "./helpers/backend";
import { expectDisplayedSurface } from "./helpers/displayedSurface";
import {
  finishMeeting,
  hideAssistantWindow,
  startMeeting,
  waitForAssistantWindow,
} from "./helpers/meetingLifecycle";
import { closeSettingsIfOpen, openSettings } from "./helpers/settings";

interface SettingsSnapshot {
  stt: Record<string, unknown>;
}

const waitOptions = { timeout: 20_000, interval: 200 };
const backendWaitOptions = { timeout: 120_000, interval: 200 };
let settingsSnapshot: SettingsSnapshot | null = null;

async function expectNoAccessibilityViolations(): Promise<void> {
  const results = await new AxeBuilder({ client: browser })
    .setLegacyMode()
    .withTags(["wcag2a", "wcag2aa"])
    .analyze();
  expect(
    results.violations.flatMap((violation) =>
      violation.nodes.map((node) => ({
        id: violation.id,
        target: node.target,
      })),
    ),
  ).toEqual([]);
}

async function restoreState(): Promise<void> {
  const cleanupErrors: unknown[] = [];
  for (const cleanup of [
    () => closeSettingsIfOpen({ discard: true, waitOptions }),
    () => finishMeeting(backendWaitOptions),
    () => hideAssistantWindow(waitOptions),
  ]) {
    try {
      await cleanup();
    } catch (error) {
      cleanupErrors.push(error);
    }
  }
  if (settingsSnapshot) {
    try {
      await localBackendRequest({
        path: "/api/settings",
        method: "POST",
        body: { stt: settingsSnapshot.stt },
      });
    } catch (error) {
      cleanupErrors.push(error);
    }
  }
  try {
    await browser.tauri.switchWindow("main");
    await browser.refresh();
    await waitForBackendReady(backendWaitOptions);
    await finishMeeting(backendWaitOptions);
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
  } catch (error) {
    cleanupErrors.push(error);
  }
  if (cleanupErrors.length > 0) {
    const details = cleanupErrors
      .map((error) => (error instanceof Error ? error.message : String(error)))
      .join("; ");
    throw new AggregateError(
      cleanupErrors,
      `Accessibility E2E cleanup failed: ${details}`,
    );
  }
}

describe("Native shell accessibility", () => {
  beforeEach(async () => {
    await browser.tauri.switchWindow("main");
    await waitForBackendReady(backendWaitOptions);
    await closeSettingsIfOpen({ discard: true, waitOptions });
    await finishMeeting(waitOptions);
    await hideAssistantWindow(waitOptions);
    settingsSnapshot = await localBackendRequest<SettingsSnapshot>({
      path: "/api/settings",
    });
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
  });

  afterEach(async () => {
    await restoreState();
    settingsSnapshot = null;
  });

  it("has no WCAG A or AA violations on setup", async () => {
    await expectNoAccessibilityViolations();
  });

  it("has no WCAG A or AA violations on support settings", async () => {
    await openSettings(waitOptions);
    await expectNoAccessibilityViolations();
    await closeSettingsIfOpen({ discard: true, waitOptions });
  });

  it("has no WCAG A or AA violations on advanced settings", async () => {
    await openSettings(waitOptions);
    const advanced = await $('//button[.//span[normalize-space()="詳細設定"]]');
    await advanced.waitForClickable(waitOptions);
    await advanced.click();
    await expectDisplayedSurface(
      'section[data-settings-page="詳細設定"]',
      waitOptions,
    );
    await expectNoAccessibilityViolations();
    await closeSettingsIfOpen({ discard: true, waitOptions });
  });

  it("has no WCAG A or AA violations on meeting controls", async () => {
    await localBackendRequest({
      path: "/api/settings",
      method: "POST",
      body: { stt: { backend: "dummy" } },
    });
    await browser.refresh();
    await waitForBackendReady(backendWaitOptions);
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
    await startMeeting(waitOptions);
    await expectNoAccessibilityViolations();
  });

  it("has no WCAG A or AA violations on the assistant window", async () => {
    await localBackendRequest({
      path: "/api/settings",
      method: "POST",
      body: { stt: { backend: "dummy" } },
    });
    await browser.refresh();
    await waitForBackendReady(backendWaitOptions);
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
    await startMeeting(waitOptions);
    await waitForAssistantWindow(waitOptions);
    await expectNoAccessibilityViolations();
  });
});
