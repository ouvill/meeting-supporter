import { readFile } from "node:fs/promises";
import { $, browser, expect } from "@wdio/globals";
import {
  createAcpFixture,
  removeAcpFixture,
  type AcpFixture,
} from "./helpers/acpFixture";
import { localBackendRequest, waitForBackendReady } from "./helpers/backend";
import { expectDisplayedSurface } from "./helpers/displayedSurface";
import {
  finishMeeting,
  hideAssistantWindow,
  startMeeting,
  waitForAssistantWindow,
} from "./helpers/meetingLifecycle";
import { closeSettingsIfOpen } from "./helpers/settings";

interface RouteCatalog {
  routes: Array<{
    id: string;
    readiness: string;
    selectable: boolean;
  }>;
  assignments: {
    reply: string | null;
    info: string | null;
    minutes: string | null;
  };
}

interface SettingsSnapshot {
  acp: { command: string[] };
  stt: Record<string, unknown>;
  reply: {
    enabled: boolean;
    auto_generate: boolean;
    default_style: string;
    styles: Array<{ id: string; enabled: boolean }>;
  };
}

const waitOptions = { timeout: 20_000, interval: 200 };
const generationWaitOptions = { ...waitOptions, timeout: 90_000 };
let settingsSnapshot: SettingsSnapshot | null = null;
let routesSnapshot: RouteCatalog | null = null;
let fixture: AcpFixture | null = null;

async function sendSyntheticSelfTurn(text: string): Promise<void> {
  await browser.tauri.execute(
    `async ({ core }, expectedText) => {
    const snapshot = await core.invoke('get_backend_bootstrap_snapshot')
    if (!snapshot || typeof snapshot !== 'object') {
      throw new Error('Tauri backend WebSocket endpoint is unavailable')
    }
    const { port, auth_token: token } = snapshot
    if (typeof port !== 'number' || typeof token !== 'string' || !token) {
      throw new Error('Tauri backend WebSocket endpoint is unavailable')
    }
    const socket = new WebSocket('ws://127.0.0.1:' + port + '/ws', ['auth.' + token])
    try {
      const { promise, resolve, reject } = Promise.withResolvers()
      const timeoutId = window.setTimeout(() => reject(new Error('Synthetic turn timed out')), 20_000)
      socket.addEventListener('open', () => {
        socket.send(JSON.stringify({ type: 'user_reply', text: expectedText }))
      })
      socket.addEventListener('error', () => reject(new Error('Synthetic turn socket failed')))
      socket.addEventListener('message', event => {
        let message
        try { message = JSON.parse(String(event.data)) } catch { return }
        if (message.type === 'stt_final' && message.role === 'self' && message.text === expectedText) {
          window.clearTimeout(timeoutId)
          resolve(undefined)
        }
      })
      await promise
    } finally {
      socket.close()
    }
  }`,
    text,
  );
}

async function waitForReplyText(expected?: string): Promise<void> {
  await browser.waitUntil(
    async () =>
      browser.tauri.execute((_tauri, expectedText) => {
        const text = document.querySelector(
          'section[aria-labelledby="cue-card-heading"] p.whitespace-pre-wrap',
        )?.textContent;
        return (
          typeof text === "string" &&
          (expectedText ? text.trim() === expectedText : text.trim().length > 0)
        );
      }, expected),
    {
      ...generationWaitOptions,
      timeoutMsg: expected
        ? `Reply did not become ${expected}`
        : "Reply generation did not produce a ready card",
    },
  );
}

async function configureFixture(initialInvocation?: number): Promise<void> {
  fixture = await createAcpFixture({ initialInvocation });
  await localBackendRequest({
    path: "/api/settings",
    method: "POST",
    body: {
      stt: { backend: "dummy" },
      reply: { enabled: true, auto_generate: false },
      acp: { command: fixture.command },
    },
  });
  const configuredRoutes = await localBackendRequest<RouteCatalog>({
    path: "/api/ai/routes",
  });
  expect(
    configuredRoutes.routes.find((route) => route.id === "acp"),
  ).toMatchObject({ readiness: "ready", selectable: true });
  await localBackendRequest({
    path: "/api/ai/routes/assignments",
    method: "PUT",
    body: { reply: "acp", info: null, minutes: null },
  });
  await browser.refresh();
  await waitForBackendReady();
  await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
  await startMeeting(waitOptions);
  await waitForAssistantWindow(waitOptions);
  await browser.refresh();
  await waitForBackendReady();
  await expectDisplayedSurface('[data-testid="live-reply-panel"]', waitOptions);
  const generate = await $('//button[normalize-space()="返答案を作る"]');
  await generate.waitForEnabled(generationWaitOptions);
}

async function prepareSyntheticTurn(text: string): Promise<void> {
  await browser.tauri.switchWindow("main");
  await sendSyntheticSelfTurn(text);
  await browser.tauri.switchWindow("assistant");
  await browser.waitUntil(
    async () =>
      browser.tauri.execute(
        (_tauri, expected) =>
          document.querySelector(
            'section[aria-labelledby="latest-utterance-heading"] p',
          )?.textContent === expected,
        text,
      ),
    {
      ...generationWaitOptions,
      timeoutMsg: "Synthetic turn did not reach the assistant window",
    },
  );
}

async function cleanupState(): Promise<void> {
  const cleanupErrors: unknown[] = [];
  for (const cleanup of [
    () => closeSettingsIfOpen({ discard: true, waitOptions }),
    () => finishMeeting(generationWaitOptions),
    () => hideAssistantWindow(waitOptions),
    async () => {
      if (routesSnapshot) {
        await localBackendRequest({
          path: "/api/ai/routes/assignments",
          method: "PUT",
          body: routesSnapshot.assignments,
        });
      }
    },
    async () => {
      if (settingsSnapshot) {
        await localBackendRequest({
          path: "/api/settings",
          method: "POST",
          body: {
            stt: settingsSnapshot.stt,
            reply: {
              enabled: settingsSnapshot.reply.enabled,
              auto_generate: settingsSnapshot.reply.auto_generate,
              default_style: settingsSnapshot.reply.default_style,
              styles: settingsSnapshot.reply.styles.map(({ id, enabled }) => ({
                id,
                enabled,
              })),
            },
            acp: { command: settingsSnapshot.acp.command },
          },
        });
      }
    },
    async () => {
      await browser.tauri.switchWindow("main");
      await browser.refresh();
      await waitForBackendReady();
      await finishMeeting(generationWaitOptions);
      await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
    },
    async () => {
      if (fixture) await removeAcpFixture(fixture);
    },
  ]) {
    try {
      await cleanup();
    } catch (error) {
      cleanupErrors.push(error);
    }
  }
  if (cleanupErrors.length > 0) {
    const details = cleanupErrors
      .map((error) => (error instanceof Error ? error.message : String(error)))
      .join("; ");
    throw new AggregateError(
      cleanupErrors,
      `Native reply cleanup failed: ${details}`,
    );
  }
}

describe("Live reply controls", () => {
  beforeEach(async () => {
    await browser.tauri.switchWindow("main");
    await waitForBackendReady();
    await closeSettingsIfOpen({ discard: true, waitOptions });
    await finishMeeting(waitOptions);
    await hideAssistantWindow(waitOptions);
    settingsSnapshot = await localBackendRequest<SettingsSnapshot>({
      path: "/api/settings",
    });
    routesSnapshot = await localBackendRequest<RouteCatalog>({
      path: "/api/ai/routes",
    });
    fixture = null;
    await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
  });

  afterEach(async () => {
    await cleanupState();
    settingsSnapshot = null;
    routesSnapshot = null;
    fixture = null;
  });

  it("stops a blocked stream and retries to a ready reply", async () => {
    await configureFixture();
    await prepareSyntheticTurn(
      "E2E停止操作用の発言です。短く返答してください。",
    );

    const generate = await $('//button[normalize-space()="返答案を作る"]');
    await generate.waitForEnabled(waitOptions);
    await generate.click();
    const stop = await $('//button[normalize-space()="停止"]');
    await stop.waitForEnabled({ ...waitOptions, timeout: 5_000 });
    if (!fixture) throw new Error("ACP fixture is missing");
    await browser.waitUntil(
      async () => {
        try {
          return (await readFile(fixture!.statePath, "utf-8")) === "1";
        } catch {
          return false;
        }
      },
      {
        ...waitOptions,
        timeoutMsg: "ACP fixture did not enter its first prompt",
      },
    );
    await stop.click();
    await $(
      '//*[normalize-space()="返答案の生成を停止しました。"]',
    ).waitForDisplayed(generationWaitOptions);

    const retry = await $('//button[normalize-space()="返答案を作る"]');
    await retry.waitForEnabled(waitOptions);
    await retry.click();
    await waitForReplyText("準備できました。進めてください。");
  });

  it("rephrases a ready reply concisely", async () => {
    await configureFixture(1);
    await prepareSyntheticTurn("E2E言い換え操作用の発言です。");
    const generate = await $('//button[normalize-space()="返答案を作る"]');
    await generate.waitForEnabled(waitOptions);
    await generate.click();
    await waitForReplyText("準備できました。進めてください。");

    const concise = await $('//button[normalize-space()="短く"]');
    await concise.waitForEnabled(waitOptions);
    await concise.click();
    await waitForReplyText("承知しました。");
  });

  it("discards a ready reply and restores the empty cue", async () => {
    await configureFixture(1);
    await prepareSyntheticTurn("E2E破棄操作用の発言です。");
    const generate = await $('//button[normalize-space()="返答案を作る"]');
    await generate.waitForEnabled(waitOptions);
    await generate.click();
    await waitForReplyText("準備できました。進めてください。");

    const discard = await $('//button[normalize-space()="破棄"]');
    await discard.waitForEnabled(waitOptions);
    await discard.click();
    await browser.waitUntil(
      async () =>
        browser.tauri.execute(
          () =>
            document
              .querySelector('section[aria-labelledby="cue-card-heading"]')
              ?.textContent?.includes(
                "必要なときに「返答案を作る」を押してください。",
              ) === true,
        ),
      {
        ...waitOptions,
        timeoutMsg: "Discard did not restore the empty reply state",
      },
    );
  });
});
