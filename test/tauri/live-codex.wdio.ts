import { $, browser, expect } from "@wdio/globals";
import { expectDisplayedSurface } from "./helpers/displayedSurface";

interface RouteState {
  id: string;
  availability: string;
  readiness: string;
  selectable: boolean;
  selected: boolean;
}

interface RouteCatalog {
  routes: RouteState[];
  assignments: {
    reply: string | null;
    info: string | null;
    minutes: string | null;
  };
}

interface SettingsSnapshot {
  stt: Record<string, unknown>;
  reply: {
    enabled: boolean;
    auto_generate: boolean;
    default_style: string;
    styles: Array<{ id: string; enabled: boolean }>;
  };
}

interface BackendRequest {
  path: string;
  method?: "GET" | "POST" | "PUT";
  body?: unknown;
}
interface StreamDigests {
  assistant: string;
}

const waitOptions = {
  timeout: 15_000,
  interval: 200,
};

const routeProbeWaitOptions = {
  ...waitOptions,
  // The route probe may consume its 15-second backend limit before startup settles.
  timeout: 30_000,
};

async function localBackendRequest<T>(request: BackendRequest): Promise<T> {
  return browser.tauri.execute(async ({ core }, rawRequest: BackendRequest) => {
    const snapshot = await core.invoke("get_backend_bootstrap_snapshot");
    if (
      !snapshot ||
      typeof snapshot !== "object" ||
      !("port" in snapshot) ||
      !("auth_token" in snapshot)
    ) {
      throw new Error(
        "Tauri backend bootstrap snapshot did not provide a usable local endpoint",
      );
    }
    const { port, auth_token: token } = snapshot;
    if (
      typeof port !== "number" ||
      !Number.isInteger(port) ||
      port <= 0 ||
      typeof token !== "string" ||
      !token
    ) {
      throw new Error(
        "Tauri backend bootstrap snapshot did not provide a usable local endpoint",
      );
    }

    const response = await fetch(`http://127.0.0.1:${port}${rawRequest.path}`, {
      method: rawRequest.method ?? "GET",
      headers: {
        Authorization: `Bearer ${token}`,
        ...(rawRequest.body === undefined
          ? {}
          : { "Content-Type": "application/json" }),
      },
      ...(rawRequest.body === undefined
        ? {}
        : { body: JSON.stringify(rawRequest.body) }),
    });
    if (!response.ok)
      throw new Error(
        `Local backend request failed with HTTP ${response.status}`,
      );
    return response.json();
  }, request) as Promise<T>;
}

async function waitForBackendReady(): Promise<void> {
  await browser.waitUntil(
    async () =>
      browser.tauri.execute(async ({ core }) => {
        const snapshot = await core.invoke("get_backend_bootstrap_snapshot");
        if (!snapshot || typeof snapshot !== "object") return false;
        return (
          "running" in snapshot &&
          snapshot.running === true &&
          "port" in snapshot &&
          typeof snapshot.port === "number" &&
          snapshot.port > 0 &&
          "auth_token" in snapshot &&
          typeof snapshot.auth_token === "string" &&
          snapshot.auth_token.length > 0
        );
      }),
    {
      ...waitOptions,
      timeoutMsg:
        "The Tauri backend did not become ready with a local authenticated endpoint",
    },
  );
}

async function requestCodexReplyStream(): Promise<StreamDigests> {
  return browser.tauri.execute<StreamDigests>(`async ({ core }) => {
    const snapshot = await core.invoke('get_backend_bootstrap_snapshot')
    if (!snapshot || typeof snapshot !== 'object') {
      throw new Error('Tauri backend bootstrap snapshot did not provide a WebSocket endpoint')
    }
    const { port, auth_token: token } = snapshot
    if (typeof port !== 'number' || typeof token !== 'string' || !token) {
      throw new Error('Tauri backend bootstrap snapshot did not provide a WebSocket endpoint')
    }

    const userReplyText = 'E2E動作確認です。短く了承を伝えてください。'
    const generationId = crypto.randomUUID()
    const digestText = async text => {
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
      const bytes = new Uint8Array(digest)
      let result = ''
      for (let index = 0; index < bytes.length; index += 1) result += bytes[index].toString(16).padStart(2, '0')
      return result
    }

    let socket = null
    try {
      const { promise, resolve, reject } = Promise.withResolvers()
      let settled = false
      let userTurnReceived = false
      let generationStarted = false
      let reply = ''
      const finish = (result, errorText) => {
        if (settled) return
        settled = true
        window.clearTimeout(timeoutId)
        if (typeof errorText === 'string') {
          reject(new Error(errorText))
        } else if (result === undefined) {
          reject(new Error('The Codex WebSocket stream did not produce a final reply'))
        } else {
          resolve(result)
        }
      }
      const timeoutId = window.setTimeout(() => finish(), 60_000)

      socket = new WebSocket('ws://127.0.0.1:' + port + '/ws', ['auth.' + token])
      socket.addEventListener('open', () => {
        socket.send(JSON.stringify({ type: 'user_reply', text: userReplyText }))
      })
      socket.addEventListener('error', () => finish())
      socket.addEventListener('close', () => {
        if (!settled) finish()
      })
      socket.addEventListener('message', async event => {
        let message
        try {
          message = JSON.parse(String(event.data))
        } catch {
          finish()
          return
        }

        if (message.type === 'error' || message.type === 'suggestion_error') {
          finish(undefined, typeof message.text === 'string' ? message.text : message.type)
          return
        }
        if (message.type === 'stt_final' && message.role === 'self' && !userTurnReceived) {
          userTurnReceived = true
          socket.send(JSON.stringify({ type: 'generate_reply', generation_id: generationId }))
          return
        }
        if (message.type === 'suggestions_start' && message.generation_id === generationId) {
          generationStarted = true
          return
        }
        if (message.type !== 'reply_chunk' || message.generation_id !== generationId) return

        if (message.final === true) {
          if (!userTurnReceived || !generationStarted || !reply.trim()) {
            finish()
            return
          }
          finish({
            assistant: await digestText(reply),
          })
          return
        }
        if (typeof message.text !== 'string') {
          finish()
          return
        }
        reply += message.text
      })
      return await promise
    } finally {
      socket?.close()
    }
  }`) as Promise<StreamDigests>;
}
async function submitSelfReplyAndWaitForSttFinal(
  userReplyText: string,
): Promise<void> {
  return browser.tauri.execute<void>(
    `async ({ core }, expectedText) => {
    const snapshot = await core.invoke('get_backend_bootstrap_snapshot')
    if (!snapshot || typeof snapshot !== 'object') {
      throw new Error('Tauri backend bootstrap snapshot did not provide a WebSocket endpoint')
    }
    const { port, auth_token: token } = snapshot
    if (typeof port !== 'number' || typeof token !== 'string' || !token) {
      throw new Error('Tauri backend bootstrap snapshot did not provide a WebSocket endpoint')
    }


    let socket = null
    try {
      const { promise, resolve, reject } = Promise.withResolvers()
      let settled = false
      const finish = success => {
        if (settled) return
        settled = true
        window.clearTimeout(timeoutId)
        if (success === true) {
          resolve()
        } else {
          reject(new Error('The follow-up user reply did not produce its self STT final'))
        }
      }
      const timeoutId = window.setTimeout(() => finish(), 60_000)

      socket = new WebSocket('ws://127.0.0.1:' + port + '/ws', ['auth.' + token])
      socket.addEventListener('open', () => {
        socket.send(JSON.stringify({ type: 'user_reply', text: expectedText }))
      })
      socket.addEventListener('error', () => finish())
      socket.addEventListener('close', () => {
        if (!settled) finish()
      })
      socket.addEventListener('message', async event => {
        let message
        try {
          message = JSON.parse(String(event.data))
        } catch {
          finish()
          return
        }

        if (message.type === 'error') {
          finish()
          return
        }
        if (
          message.type !== 'stt_final' ||
          message.role !== 'self' ||
          message.text !== expectedText
        ) {
          return
        }
        finish(true)
      })
      return await promise
    } finally {
      socket?.close()
    }
  }`,
    userReplyText,
  ) as Promise<void>;
}

async function assistantCueMatchesDigest(
  expectedDigest: string,
): Promise<boolean> {
  return browser.tauri.execute<boolean>(
    `async (_tauri, digest) => {
    const cue = document.querySelector('section[aria-labelledby="cue-card-heading"] p.whitespace-pre-wrap')
    const text = cue?.textContent
    if (!text) return false
    const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
    const bytes = new Uint8Array(hash)
    let actualDigest = ''
    for (let index = 0; index < bytes.length; index += 1) actualDigest += bytes[index].toString(16).padStart(2, '0')
    return actualDigest === digest
  }`,
    expectedDigest,
  ) as Promise<boolean>;
}


async function finishMeetingFromMain(): Promise<void> {
  await browser.tauri.switchWindow("main");
  const endButton = await $('button[aria-label="会議を終了"]');
  if (!(await endButton.isExisting())) return;

  await endButton.click();
  const confirmButton = await $('//button[normalize-space()="終了する"]');
  await confirmButton.waitForExist(waitOptions);
  await confirmButton.click();
  await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
}

async function hideAssistantWindow(): Promise<void> {
  await browser.tauri.switchWindow("main");
  await browser.tauri.execute(({ core }) =>
    core.invoke("set_assistant_window_visible", { visible: false }),
  );
}

describe("Tauri smoke", () => {
  it("runs a ready Codex reply through the real multi-window desktop flow", async () => {
    let settingsSnapshot: SettingsSnapshot | null = null;
    let routesSnapshot: RouteCatalog | null = null;
    let settingsChanged = false;
    let assignmentsChanged = false;
    let meetingStarted = false;

    try {
      await browser.tauri.switchWindow("main");
      expect(await browser.tauri.listWindows()).toContain("main");

      await browser.waitUntil(
        async () =>
          browser.tauri.execute(() => {
            const global = globalThis as typeof globalThis & {
              wdioTauri?: unknown;
              __TAURI__?: unknown;
            };
            return Boolean(global.wdioTauri && global.__TAURI__);
          }),
        {
          ...waitOptions,
          timeoutMsg:
            "WDIO frontend plugin and global Tauri API were not available",
        },
      );
      await waitForBackendReady();

      const productNavigation = await $('nav[aria-label="アプリツールバー"]');
      await productNavigation.waitForDisplayed(waitOptions);
      const pinButton = await $('button[title^="常に前面"]');
      await pinButton.waitForDisplayed(waitOptions);
      const initialPinState = await pinButton.getAttribute("aria-pressed");
      expect(["true", "false"]).toContain(initialPinState);
      await $('//button[normalize-space()="履歴"]').click();
      await expectDisplayedSurface(
        '[data-testid="meeting-history-screen"]',
        waitOptions,
      );
      await $('//button[@aria-label="会議履歴を閉じて戻る"]').click();
      await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);

      const startButton = await $('//button[normalize-space()="会議を開始"]');
      await startButton.waitForExist(waitOptions);
      await $('//button[normalize-space()="設定"]').click();
      await expectDisplayedSurface(
        '[data-testid="settings-modal"]',
        waitOptions,
      );
      await $('//h2[normalize-space()="設定"]').waitForDisplayed(waitOptions);

      const managedCard = await $(
        '//h5[normalize-space()="アプリにおまかせ"]/ancestor::div[contains(@class, "rounded-xl")][1]',
      );
      await managedCard.waitForExist(routeProbeWaitOptions);
      expect(await managedCard.getText()).toContain("現在は利用できません");
      expect(await managedCard.getText()).toContain(
        "このビルドではMeeting Supporter AIを提供していません",
      );
      expect(
        await managedCard
          .$('.//button[normalize-space()="この方法を選ぶ"]')
          .isExisting(),
      ).toBe(false);

      const codexCard = await $(
        '//h5[normalize-space()="ChatGPT の契約を使う"]/ancestor::div[contains(@class, "rounded-xl")][1]',
      );
      await codexCard.waitForExist(waitOptions);
      expect(await codexCard.getText()).toContain("利用できます");
      await $('//button[.//span[normalize-space()="このアプリ"]]').click();
      await expectDisplayedSurface(
        'section[data-settings-page="このアプリについて"]',
        waitOptions,
      );
      await $(
        '//summary[normalize-space()="アプリケーションライセンス（AGPL-3.0-only）"]',
      ).click();
      const applicationLicense = await $('[data-testid="application-license"]');
      await browser.waitUntil(
        async () =>
          browser.execute(
            (element, expected) =>
              element.textContent?.includes(expected) ?? false,
            applicationLicense,
            "GNU AFFERO GENERAL PUBLIC LICENSE",
          ),
        {
          ...waitOptions,
          timeoutMsg:
            "Bundled AGPL application license was not readable from settings",
        },
      );
      await $(
        '//summary[normalize-space()="THIRD-PARTY-NOTICESを表示"]',
      ).click();
      const thirdPartyNotices = await $('[data-testid="third-party-notices"]');
      await browser.waitUntil(
        async () =>
          browser.execute(
            (element, expected) =>
              element.textContent?.includes(expected) ?? false,
            thirdPartyNotices,
            "uv 0.11.7",
          ),
        {
          ...waitOptions,
          timeoutMsg:
            "Bundled third-party notices were not readable from settings",
        },
      );

      await $('button[aria-label="設定を閉じる"]').click();

      settingsSnapshot = await localBackendRequest<SettingsSnapshot>({
        path: "/api/settings",
      });
      routesSnapshot = await localBackendRequest<RouteCatalog>({
        path: "/api/ai/routes",
      });
      const managedRoute = routesSnapshot.routes.find(
        (route) => route.id === "managed",
      );
      expect(managedRoute).toMatchObject({
        availability: "experimental",
        readiness: "not_offered",
        selectable: false,
        selected: false,
      });
      const codexRoute = routesSnapshot.routes.find(
        (route) => route.id === "codex",
      );
      expect(codexRoute).toMatchObject({
        availability: "experimental",
        readiness: "ready",
        selectable: true,
      });

      await localBackendRequest({
        path: "/api/settings",
        method: "POST",
        body: {
          stt: { backend: "dummy" },
          reply: { enabled: true, auto_generate: false },
        },
      });
      settingsChanged = true;
      let configuredRoutes = routesSnapshot;
      if (routesSnapshot.assignments.reply !== "codex") {
        configuredRoutes = await localBackendRequest<RouteCatalog>({
          path: "/api/ai/routes/assignments",
          method: "PUT",
          body: { reply: "codex", info: null, minutes: null },
        });
        assignmentsChanged = true;
      }
      expect(
        configuredRoutes.routes.find((route) => route.id === "codex"),
      ).toMatchObject({
        selected: true,
        readiness: "ready",
        selectable: true,
      });

      const refreshedRoutes = await localBackendRequest<RouteCatalog>({
        path: "/api/ai/routes",
      });
      expect(
        refreshedRoutes.routes.find((route) => route.id === "codex"),
      ).toMatchObject({
        selected: true,
        readiness: "ready",
        selectable: true,
      });

      await browser.refresh();
      await waitForBackendReady();
      await expectDisplayedSurface('[data-testid="setup-screen"]', waitOptions);
      const audioPreparationButton = await $(
        '//button[normalize-space()="音声を準備する"]',
      );
      if (await audioPreparationButton.isExisting()) {
        await $(
          '//*[normalize-space()="先に音声を準備してください"]',
        ).waitForExist(waitOptions);
        await audioPreparationButton.click();
        await $('//*[normalize-space()="音声の準備ができました"]').waitForExist(
          waitOptions,
        );
      }
      const refreshedStartButton = await $(
        '//button[normalize-space()="会議を開始"]',
      );
      await refreshedStartButton.waitForEnabled(waitOptions);
      await refreshedStartButton.click();
      meetingStarted = true;
      await expectDisplayedSurface(
        '[data-testid="meeting-control-screen"]',
        waitOptions,
      );
      await browser.waitUntil(
        async () => (await browser.tauri.listWindows()).includes("assistant"),
        {
          ...waitOptions,
          timeoutMsg:
            "Starting a meeting did not expose the assistant Tauri window",
        },
      );

      await browser.tauri.switchWindow("assistant");
      await expectDisplayedSurface(
        '[data-testid="live-reply-panel"]',
        waitOptions,
      );
      await $('//h1[normalize-space()="会話プロンプター"]').waitForDisplayed(
        waitOptions,
      );
      await $('//header//*[normalize-space()="会議中"]').waitForDisplayed(
        waitOptions,
      );
      const cueReadyText = await browser.tauri.execute(
        () =>
          document.querySelector('section[aria-labelledby="cue-card-heading"]')
            ?.textContent ?? "",
      );
      expect(cueReadyText).toContain(
        "必要なときに「返答案を作る」を押してください。",
      );
      const generateButton = await $(
        '//button[normalize-space()="返答案を作る"]',
      );
      await generateButton.waitForEnabled(waitOptions);

      await browser.tauri.switchWindow("main");
      const streamDigests = await requestCodexReplyStream();

      await browser.tauri.switchWindow("assistant");
      await browser.waitUntil(
        async () => assistantCueMatchesDigest(streamDigests.assistant),
        {
          timeout: 20_000,
          interval: 200,
          timeoutMsg:
            "The assistant Cue did not display the completed Codex stream result",
        },
      );
      expect(await generateButton.isEnabled()).toBe(true);

      await browser.tauri.switchWindow("main");
      const followUpSelfReplyText =
        "E2E追加発話です。完了済みの返答案を保持してください。";
      await submitSelfReplyAndWaitForSttFinal(followUpSelfReplyText);

      await browser.tauri.switchWindow("assistant");
      await browser.waitUntil(
        async () =>
          browser.tauri.execute<boolean>(
            (_tauri, expectedText) =>
              document.querySelector(
                'section[aria-labelledby="latest-utterance-heading"] p',
              )?.textContent === expectedText,
            followUpSelfReplyText,
          ) as Promise<boolean>,
        {
          ...waitOptions,
          timeoutMsg:
            "The assistant window did not receive the follow-up self STT final",
        },
      );
      await browser.waitUntil(
        async () => assistantCueMatchesDigest(streamDigests.assistant),
        {
          ...waitOptions,
          timeoutMsg:
            "The completed Codex stream result did not remain in the assistant Cue after the follow-up STT final",
        },
      );

      await finishMeetingFromMain();
      meetingStarted = false;
    } finally {
      try {
        if (meetingStarted) await finishMeetingFromMain();
      } finally {
        try {
          if (meetingStarted) await hideAssistantWindow();
        } finally {
          try {
            if (assignmentsChanged && routesSnapshot) {
              await localBackendRequest({
                path: "/api/ai/routes/assignments",
                method: "PUT",
                body: routesSnapshot.assignments,
              });
            }
          } finally {
            if (settingsChanged && settingsSnapshot) {
              await localBackendRequest({
                path: "/api/settings",
                method: "POST",
                body: {
                  stt: settingsSnapshot.stt,
                  reply: {
                    enabled: settingsSnapshot.reply.enabled,
                    auto_generate: settingsSnapshot.reply.auto_generate,
                    default_style: settingsSnapshot.reply.default_style,
                    styles: settingsSnapshot.reply.styles.map(
                      ({ id, enabled }) => ({ id, enabled }),
                    ),
                  },
                },
              });
            }
          }
        }
      }
    }
  });
});
