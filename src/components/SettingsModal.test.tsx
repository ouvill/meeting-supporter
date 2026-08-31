import { useRef, useState } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  saveSettingsApiSettingsPost,
  testConnectionApiSettingsConnectionsTestPost,
} from "../api/generated/sdk.gen";
import type {
  SettingsResponse,
  SpeechModelStatusResponse,
} from "../api/generated/types.gen";
import type {
  AiRouteReadModel,
  AiRoutesController,
} from "../hooks/useAiRoutes";
import { SettingsModal } from "./SettingsModal";

const sdkMocks = vi.hoisted(() => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  testConnection: vi.fn(),
  getSpeechStatus: vi.fn(),
  startSpeechDownload: vi.fn(),
  cancelSpeechDownload: vi.fn(),
  getOllamaModels: vi.fn(),
}));

const managedMocks = vi.hoisted(() => ({
  authStatus: vi.fn(),
  entitlement: vi.fn(),
  onAuthChanged: vi.fn(),
  startAuth: vi.fn(),
  logout: vi.fn(),
  checkout: vi.fn(),
  billing: vi.fn(),
  deleteAccount: vi.fn(),
}));

const licenseMocks = vi.hoisted(() => ({
  read: vi.fn(),
}));
vi.mock("../api/generated/sdk.gen", () => ({
  getSettingsApiSettingsGet: sdkMocks.getSettings,
  saveSettingsApiSettingsPost: sdkMocks.saveSettings,
  testConnectionApiSettingsConnectionsTestPost: sdkMocks.testConnection,
  getSpeechModelStatusApiSttModelGet: sdkMocks.getSpeechStatus,
  startSpeechModelDownloadApiSttModelDownloadPost: sdkMocks.startSpeechDownload,
  cancelSpeechModelDownloadApiSttModelCancelPost: sdkMocks.cancelSpeechDownload,
  getOllamaModelsApiSettingsOllamaModelsGet: sdkMocks.getOllamaModels,
}));
vi.mock("../platform/managedServiceClient", () => ({
  getManagedAuthStatus: managedMocks.authStatus,
  getManagedEntitlement: managedMocks.entitlement,
  onManagedAuthChanged: managedMocks.onAuthChanged,
  startManagedAuth: managedMocks.startAuth,
  logoutManagedAuth: managedMocks.logout,
  openManagedCheckout: managedMocks.checkout,
  openManagedBillingPortal: managedMocks.billing,
  deleteManagedAccount: managedMocks.deleteAccount,
}));
vi.mock("../../LICENSE?raw", () => ({
  get default() {
    return licenseMocks.read();
  },
}));
vi.mock("../api/recordingRetention", () => ({
  previewRecordingCleanup: vi.fn(),
  executeRecordingCleanup: vi.fn(),
}));

const response = new Response(null, { status: 200 });
const request = new Request("http://localhost/api/settings");
function settings(overrides: Partial<SettingsResponse> = {}): SettingsResponse {
  return {
    ollama: { base_url: "http://127.0.0.1:11434/v1" },
    acp: { command: [], runtime: "acp", capabilities: ["reply"] },
    stt: { backend: "whisper", language: "ja" },
    agents: { info_enabled: true },
    reply: {
      enabled: true,
      auto_generate: false,
      default_style: "standard",
      styles: [{ id: "standard", label: "標準", enabled: true, priority: 10 }],
    },
    secrets: {},
    providers: [],
    data_dir: "/tmp/data",
    context_dir: "/tmp/context",
    usage: {
      budget: {},
      current_meeting: {},
      current_month: {},
      billing_mode: "external_subscription",
    },
    recording_retention: {},
    ...overrides,
  };
}
function speechStatus(): SpeechModelStatusResponse {
  return {
    backend: "whisper",
    model_id: "large-v3-turbo",
    state: "ready",
    phase: "idle",
    language: "ja",
    downloaded_bytes: 0,
    total_bytes: null,
    progress_percent: null,
    model_path: null,
    storage_path: "/tmp/speech",
    error_code: null,
    message: "",
    retryable: true,
    cancelable: false,
  };
}
function route(overrides: Partial<AiRouteReadModel> = {}): AiRouteReadModel {
  return {
    id: "codex",
    kind: "subscription_app",
    label: "Codex",
    description: "ChatGPT subscription",
    availability: "experimental",
    readiness: "ready",
    selectable: true,
    selected: true,
    data_location: "external",
    billing_owner: "external_subscription",
    capabilities: ["reply"],
    reason_code: null,
    message: "",
    action: "none",
    ...overrides,
  };
}
function routeCatalog(
  overrides: Partial<AiRoutesController> = {},
): AiRoutesController {
  return {
    routes: [route()],
    assignments: { reply: "codex", info: null, minutes: null },
    assignedRoutes: { reply: route(), info: null, minutes: null },
    replyStatus: { readiness: "ready", canGenerate: true, message: null },
    infoRouteStatus: {
      readiness: "setup_required",
      canGenerate: false,
      message: "会話メモを利用する支援方法を設定してください。",
    },
    minutesRouteStatus: {
      readiness: "setup_required",
      canGenerate: false,
      message: "議事録を利用する支援方法を設定してください。",
    },
    draftAssignments: { reply: "codex", info: null, minutes: null },
    assignmentDirty: false,
    loading: false,
    saving: false,
    error: null,
    manualReloadStatus: "idle",
    setDraftAssignment: vi.fn(),
    resetDraftAssignments: vi.fn(),
    reload: vi.fn(),
    saveAssignments: vi.fn().mockResolvedValue(true),
    ...overrides,
  };
}

async function renderModal(
  initial = settings(),
  routes = routeCatalog(),
  onClose = vi.fn(),
  audioSettingsLocked = false,
) {
  sdkMocks.getSettings.mockResolvedValueOnce({
    data: initial,
    error: undefined,
    request,
    response,
  });
  const rendered = render(
    <SettingsModal
      onClose={onClose}
      routes={routes}
      audioSettingsLocked={audioSettingsLocked}
    />,
  );
  await screen.findByText("支援方法");
  return { ...rendered, onClose };
}
afterEach(() => vi.unstubAllGlobals());

describe("SettingsModal connection UX", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    licenseMocks.read.mockReturnValue("GNU AFFERO GENERAL PUBLIC LICENSE");
    managedMocks.authStatus.mockResolvedValue({
      authenticated: true,
      reason: "authenticated",
    });
    managedMocks.entitlement.mockResolvedValue({
      account: { status: "active" },
      plan: { status: "active", cancel_at_period_end: false },
      quota: {
        remaining_micro_usd: 1,
        approximate_remaining_jpy: 1,
        renews_at: null,
        shared: true,
        rollover: false,
        overage_charging: false,
      },
      managed: {
        availability: "available",
        readiness: "ready",
        reason: "ready",
        action: null,
        reply: { enabled: true, selectable: true },
        speech_recognition: { enabled: true, selectable: true },
      },
    });
    managedMocks.onAuthChanged.mockResolvedValue(() => undefined);
    sdkMocks.getSpeechStatus.mockResolvedValue({
      data: speechStatus(),
      error: undefined,
      request,
      response,
    });
    sdkMocks.saveSettings.mockResolvedValue({
      data: { ok: true, settings: settings() },
      error: undefined,
      request,
      response,
    });
    sdkMocks.testConnection.mockResolvedValue({
      data: {
        ok: true,
        status: "verified",
        message: "OpenAI connection verified",
      },
      error: undefined,
      request,
      response,
    });
  });

  it("defers managed availability until Audio and does not refresh on ordinary rerenders", async () => {
    const routes = routeCatalog({
      routes: [
        route({
          id: "managed",
          kind: "managed",
          label: "Meeting Supporter",
          description: "Managed speech recognition",
          capabilities: ["reply"],
          reason_code: null,
        }),
      ],
    });
    const { onClose, rerender } = await renderModal(settings(), routes);

    expect(screen.getByRole("heading", { name: "支援方法" })).toBeInTheDocument();
    expect(managedMocks.authStatus).not.toHaveBeenCalled();
    expect(managedMocks.entitlement).not.toHaveBeenCalled();
    expect(managedMocks.onAuthChanged).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /音声/ }));

    await waitFor(() => {
      expect(managedMocks.authStatus).toHaveBeenCalledOnce();
      expect(managedMocks.entitlement).toHaveBeenCalledOnce();
      expect(managedMocks.onAuthChanged).toHaveBeenCalledOnce();
    });

    rerender(<SettingsModal onClose={onClose} routes={routes} />);
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 0);
    });

    expect(managedMocks.authStatus).toHaveBeenCalledOnce();
    expect(managedMocks.entitlement).toHaveBeenCalledOnce();
    expect(managedMocks.onAuthChanged).toHaveBeenCalledOnce();
  });

  it("ignores an auth refresh that settles after leaving and reentering Audio", async () => {
    const listeners: Array<() => void> = [];
    managedMocks.onAuthChanged.mockImplementation(
      async (listener: () => void) => {
        listeners.push(listener);
        return () => undefined;
      },
    );
    const initialAuth = { authenticated: true, reason: "authenticated" };
    let resolveStaleAuth:
      | ((status: { authenticated: boolean; reason: string }) => void)
      | undefined;
    const staleAuth = new Promise<{
      authenticated: boolean;
      reason: string;
    }>((resolve) => {
      resolveStaleAuth = resolve;
    });
    managedMocks.authStatus
      .mockResolvedValueOnce(initialAuth)
      .mockReturnValueOnce(staleAuth)
      .mockResolvedValue(initialAuth);
    const routes = routeCatalog({
      routes: [
        route({
          id: "managed",
          kind: "managed",
          label: "Meeting Supporter",
          description: "Managed speech recognition",
          capabilities: ["reply"],
          reason_code: null,
        }),
      ],
    });
    await renderModal(settings(), routes);

    fireEvent.click(screen.getByRole("button", { name: /音声/ }));
    expect(
      await screen.findByText("月額プランの共通利用枠で利用できます。"),
    ).toBeInTheDocument();
    await waitFor(() => expect(listeners).toHaveLength(1));

    act(() => listeners[0]!());
    await waitFor(() => expect(managedMocks.authStatus).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("button", { name: /支援方法/ }));
    fireEvent.click(screen.getByRole("button", { name: /音声/ }));
    await waitFor(() => {
      expect(managedMocks.authStatus).toHaveBeenCalledTimes(3);
      expect(managedMocks.entitlement).toHaveBeenCalledTimes(2);
      expect(listeners).toHaveLength(2);
    });

    await act(async () => {
      resolveStaleAuth?.({ authenticated: false, reason: "logged_out" });
      await staleAuth;
    });

    expect(routes.reload).not.toHaveBeenCalled();
    expect(managedMocks.entitlement).toHaveBeenCalledTimes(2);
    expect(
      screen.getByText("月額プランの共通利用枠で利用できます。"),
    ).toBeInTheDocument();
  });

  it("focuses the native dialog title and restores the opening control after close", async () => {
    sdkMocks.getSettings.mockResolvedValueOnce({
      data: settings(),
      error: undefined,
      request,
      response,
    });
    const routes = routeCatalog();

    function Harness() {
      const [open, setOpen] = useState(false);
      const triggerRef = useRef<HTMLButtonElement>(null);
      return (
        <>
          <button ref={triggerRef} type="button" onClick={() => setOpen(true)}>
            設定を開く
          </button>
          {open && (
            <SettingsModal
              onClose={() => setOpen(false)}
              routes={routes}
              restoreFocusTo={triggerRef.current}
            />
          )}
        </>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "設定を開く" });
    fireEvent.click(trigger);

    await screen.findByText("支援方法");
    expect(screen.getByRole("heading", { name: "設定" })).toHaveFocus();
    fireEvent.click(screen.getByLabelText("設定を閉じる"));
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("shares an OpenAI draft across Support and Audio, tests it once, and saves it globally", async () => {
    const openaiRoute = route({
      id: "openai",
      kind: "byok",
      label: "OpenAI API",
      description: "OpenAI BYOK",
      readiness: "setup_required",
      selected: true,
    });
    await renderModal(
      settings({ stt: { backend: "openai", language: "ja" } }),
      routeCatalog({
        routes: [openaiRoute],
        draftAssignments: { reply: "openai", info: null, minutes: null },
      }),
    );

    fireEvent.change(screen.getByLabelText("OpenAI APIキー"), {
      target: { value: "test-only-openai-key" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "OpenAI 接続を確認" }),
    );

    await waitFor(() =>
      expect(testConnectionApiSettingsConnectionsTestPost).toHaveBeenCalledWith(
        {
          body: { provider: "openai", api_key: "test-only-openai-key" },
        },
      ),
    );
    expect(testConnectionApiSettingsConnectionsTestPost).toHaveBeenCalledOnce();
    expect(saveSettingsApiSettingsPost).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /音声/ }));
    expect(await screen.findByLabelText("OpenAI APIキー")).toHaveValue(
      "test-only-openai-key",
    );
    expect(screen.getByText("OpenAI connection verified")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(saveSettingsApiSettingsPost).toHaveBeenCalledWith(
        expect.objectContaining({
          body: expect.objectContaining({
            secrets: { OPENAI_API_KEY: "test-only-openai-key" },
          }),
        }),
      ),
    );
  });

  it.each([
    {
      provider: "gemini",
      routeLabel: "Gemini API",
      inputLabel: "Google Gemini APIキー",
      actionLabel: "Google Gemini 接続を確認",
    },
    {
      provider: "openai",
      routeLabel: "OpenAI API",
      inputLabel: "OpenAI APIキー",
      actionLabel: "OpenAI 接続を確認",
    },
    {
      provider: "anthropic",
      routeLabel: "Anthropic API",
      inputLabel: "Anthropic APIキー",
      actionLabel: "Anthropic 接続を確認",
    },
  ])(
    "tests the $provider credential directly from its Support card",
    async ({ provider, routeLabel, inputLabel, actionLabel }) => {
      const providerRoute = route({
        id: provider,
        kind: "byok",
        label: routeLabel,
        description: `${routeLabel} BYOK`,
        readiness: "setup_required",
        selected: true,
      });
      await renderModal(
        settings({ stt: { backend: "dummy", language: "ja" } }),
        routeCatalog({
          routes: [providerRoute],
          draftAssignments: { reply: provider, info: null, minutes: null },
        }),
      );
      const draft = `${provider}-inline-key`;

      fireEvent.change(screen.getByLabelText(inputLabel), {
        target: { value: draft },
      });
      fireEvent.click(screen.getByRole("button", { name: actionLabel }));

      await waitFor(() =>
        expect(
          testConnectionApiSettingsConnectionsTestPost,
        ).toHaveBeenCalledWith({
          body: { provider, api_key: draft },
        }),
      );
      expect(testConnectionApiSettingsConnectionsTestPost).toHaveBeenCalledOnce();
      expect(saveSettingsApiSettingsPost).not.toHaveBeenCalled();
    },
  );

  it.each([
    {
      provider: "deepgram",
      inputLabel: "Deepgram APIキー",
      actionLabel: "Deepgram 接続を確認",
    },
    {
      provider: "openai",
      inputLabel: "OpenAI APIキー",
      actionLabel: "OpenAI 接続を確認",
    },
    {
      provider: "xai",
      inputLabel: "Grok / xAI APIキー",
      actionLabel: "Grok / xAI 接続を確認",
    },
  ])(
    "tests the $provider credential directly from Audio",
    async ({ provider, inputLabel, actionLabel }) => {
      await renderModal(
        settings({ stt: { backend: provider, language: "ja" } }),
      );
      fireEvent.click(screen.getByRole("button", { name: /音声/ }));
      const draft = `${provider}-audio-key`;

      fireEvent.change(await screen.findByLabelText(inputLabel), {
        target: { value: draft },
      });
      fireEvent.click(screen.getByRole("button", { name: actionLabel }));

      await waitFor(() =>
        expect(
          testConnectionApiSettingsConnectionsTestPost,
        ).toHaveBeenCalledWith({
          body: { provider, api_key: draft },
        }),
      );
      expect(testConnectionApiSettingsConnectionsTestPost).toHaveBeenCalledOnce();
      expect(saveSettingsApiSettingsPost).not.toHaveBeenCalled();
    },
  );

  it("schedules and cancels a saved key deletion before global save", async () => {
    await renderModal(
      settings({
        stt: { backend: "dummy", language: "ja" },
        secrets: { OPENAI_API_KEY: true },
      }),
      routeCatalog({
        routes: [
          route({
            id: "openai",
            kind: "byok",
            label: "OpenAI API",
            description: "OpenAI BYOK",
            selected: false,
          }),
          route(),
        ],
      }),
    );

    const scheduleDeletion = () => {
      fireEvent.click(
        screen.getByRole("button", { name: "OpenAI APIキーを削除" }),
      );
      fireEvent.click(
        screen.getByRole("button", {
          name: "OpenAI APIキーを削除予定にする",
        }),
      );
    };

    scheduleDeletion();
    expect(saveSettingsApiSettingsPost).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", {
        name: "OpenAI APIキーの削除を取り消す",
      }),
    );
    expect(
      screen.queryByRole("button", {
        name: "OpenAI APIキーの削除を取り消す",
      }),
    ).not.toBeInTheDocument();

    scheduleDeletion();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(saveSettingsApiSettingsPost).toHaveBeenCalledWith(
        expect.objectContaining({
          body: expect.objectContaining({
            delete_secrets: ["OPENAI_API_KEY"],
          }),
        }),
      ),
    );
  });

  it("keeps cloud models in Advanced without an API connection list", async () => {
    await renderModal(settings({ stt: { backend: "openai", language: "ja" } }));
    fireEvent.click(screen.getByRole("button", { name: /詳細設定/ }));

    expect(screen.queryByText("API接続")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("OpenAI APIキー")).not.toBeInTheDocument();
    expect(await screen.findByLabelText("OpenAIモデル")).toBeInTheDocument();
  });

  it("shows every route in general or setup groups and keeps the selected route visible", async () => {
    await renderModal(
      settings(),
      routeCatalog({
        routes: [
          route({
            id: "managed",
            kind: "managed",
            label: "Managed",
            description: "managed",
            readiness: "not_offered",
            selectable: false,
          }),
          route({
            id: "openai",
            kind: "byok",
            label: "OpenAI API",
            description: "BYOK",
            readiness: "setup_required",
            selected: true,
          }),
          route({
            id: "ollama",
            kind: "local",
            label: "Ollama",
            description: "local",
            readiness: "setup_required",
          }),
          route({
            id: "acp",
            kind: "local",
            label: "ACP",
            description: "agent",
            readiness: "setup_required",
          }),
        ],
        draftAssignments: { reply: "openai", info: null, minutes: null },
      }),
    );
    expect(screen.getByRole("heading", { name: "一般" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "要設定" })).toBeInTheDocument();
    expect(screen.getByText("アプリにおまかせ")).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: "返答案" }).some(
        (button) => button.getAttribute("aria-pressed") === "true",
      ),
    ).toBe(true);
    expect(screen.getByText("Ollama")).toBeInTheDocument();
    expect(screen.getByText("ACP")).toBeInTheDocument();
  });

  it("closes unchanged incomplete settings without showing a warning", async () => {
    const onClose = vi.fn();
    await renderModal(
      settings({ stt: { backend: "dummy", language: "ja" } }),
      routeCatalog({
        routes: [
          route({
            id: "openai",
            kind: "byok",
            label: "OpenAI API",
            description: "BYOK",
            readiness: "setup_required",
            selected: true,
          }),
        ],
        draftAssignments: { reply: "openai", info: null, minutes: null },
      }),
      onClose,
    );

    fireEvent.click(screen.getByLabelText("設定を閉じる"));

    expect(onClose).toHaveBeenCalledOnce();
    expect(
      screen.queryByRole("dialog", { name: "変更を破棄しますか？" }),
    ).not.toBeInTheDocument();
  });

  it("keeps dirty drafts on return, then resets every draft and closes once on discard", async () => {
    const onClose = vi.fn();
    const resetDraftAssignments = vi.fn();
    await renderModal(
      settings(),
      routeCatalog({
        routes: [
          route(),
          route({
            id: "gemini",
            kind: "byok",
            label: "Gemini API",
            description: "Gemini BYOK",
            selected: false,
          }),
        ],
        assignmentDirty: true,
        resetDraftAssignments,
      }),
      onClose,
    );

    fireEvent.click(
      screen.getByRole("checkbox", { name: "発話ごとに自動で作る" }),
    );
    fireEvent.change(screen.getByLabelText("Google Gemini APIキー"), {
      target: { value: "gemini-unsaved-draft" },
    });
    fireEvent.click(screen.getByLabelText("設定を閉じる"));
    expect(onClose).not.toHaveBeenCalled();
    expect(
      screen.getByRole("dialog", { name: "変更を破棄しますか？" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "設定に戻る" }));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Google Gemini APIキー")).toHaveValue(
      "gemini-unsaved-draft",
    );
    expect(
      screen.getByRole("checkbox", { name: "発話ごとに自動で作る" }),
    ).toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
    fireEvent.click(
      screen.getByRole("button", { name: "変更を破棄して閉じる" }),
    );

    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(resetDraftAssignments).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("Google Gemini APIキー")).toHaveValue("");
    expect(
      screen.getByRole("checkbox", { name: "発話ごとに自動で作る" }),
    ).not.toBeChecked();
  });


  it("keeps a missing BYOK credential on its selected Support card", async () => {
    const openaiRoute = route({
      id: "openai",
      kind: "byok",
      label: "OpenAI API",
      description: "BYOK",
      readiness: "setup_required",
      selected: true,
    });
    await renderModal(
      settings({ stt: { backend: "dummy", language: "ja" } }),
      routeCatalog({
        routes: [openaiRoute],
        draftAssignments: { reply: "openai", info: null, minutes: null },
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(sdkMocks.saveSettings).not.toHaveBeenCalled();
    expect(screen.getByLabelText("OpenAI APIキー")).toBeInTheDocument();
    expect(
      screen.getByText(
        "この支援方法を利用するには、利用可能なAPIキーが必要です。",
      ),
    ).toBeInTheDocument();
    expect(document.querySelector('[data-route-id="openai"]')).toHaveTextContent(
      "この支援方法を利用するには、利用可能なAPIキーが必要です。",
    );
    expect(
      screen
        .getAllByRole("alert")
        .some((alert) =>
          alert.textContent?.includes(
            "保存する前に、入力が必要な項目を確認してください。",
          ),
        ),
    ).toBe(true);
  });

  it("validates credentials for every route selected by info or minutes", async () => {
    const openaiRoute = route({
      id: "openai",
      kind: "byok",
      label: "OpenAI API",
      capabilities: ["info"],
    });
    const geminiRoute = route({
      id: "gemini",
      kind: "byok",
      label: "Gemini API",
      capabilities: ["minutes"],
    });
    await renderModal(
      settings({
        stt: { backend: "dummy", language: "ja" },
        secrets: { OPENAI_API_KEY: true },
      }),
      routeCatalog({
        routes: [openaiRoute, geminiRoute],
        assignments: { reply: null, info: "openai", minutes: "gemini" },
        draftAssignments: {
          reply: null,
          info: "openai",
          minutes: "gemini",
        },
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(sdkMocks.saveSettings).not.toHaveBeenCalled();
    expect(
      screen.getAllByText(
        "この支援方法を利用するには、利用可能なAPIキーが必要です。",
      ),
    ).not.toHaveLength(0);
  });

  it("saves settings when reply has no assigned route", async () => {
    const saveAssignments = vi.fn().mockResolvedValue(true);
    await renderModal(
      settings({ stt: { backend: "dummy", language: "ja" } }),
      routeCatalog({
        assignments: { reply: null, info: null, minutes: null },
        draftAssignments: { reply: null, info: null, minutes: null },
        assignmentDirty: true,
        saveAssignments,
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(sdkMocks.saveSettings).toHaveBeenCalledOnce());
    expect(saveAssignments).toHaveBeenCalledOnce();
  });

  it("preserves hidden nonzero budget limits when saving unrelated settings", async () => {
    await renderModal(
      settings({
        stt: { backend: "dummy", language: "ja" },
        usage: {
          budget: {
            meeting_limit_jpy: 40,
            monthly_limit_jpy: 300,
          },
          current_meeting: { estimated_cost_jpy: 12 },
          current_month: { estimated_cost_jpy: 120 },
          billing_mode: "external_subscription",
        },
      }),
    );

    fireEvent.click(
      screen.getByRole("checkbox", { name: "発話ごとに自動で作る" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(sdkMocks.saveSettings).toHaveBeenCalledOnce());
    expect(sdkMocks.saveSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({
          usage_budget: {
            meeting_limit_jpy: 40,
            monthly_limit_jpy: 300,
          },
        }),
      }),
    );
  });

  it("moves a missing cloud STT credential to its Audio control", async () => {
    await renderModal(
      settings({ stt: { backend: "openai", language: "ja" } }),
      routeCatalog(),
    );

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(sdkMocks.saveSettings).not.toHaveBeenCalled();
    expect(screen.getByLabelText("OpenAI APIキー")).toBeInTheDocument();
    expect(
      screen.getByText(
        "クラウド音声認識を利用するには、利用可能なAPIキーが必要です。",
      ),
    ).toBeInTheDocument();
  });

  it("keeps credentials at their use surface and reports partial route save success", async () => {
    const routes = routeCatalog({
      assignmentDirty: true,
      saveAssignments: vi.fn().mockResolvedValue(false),
    });
    await renderModal(
      settings({
        stt: { backend: "openai", language: "ja" },
        secrets: { OPENAI_API_KEY: true },
      }),
      routes,
    );
    fireEvent.click(screen.getByRole("button", { name: /音声/ }));
    expect(
      await screen.findByRole("button", { name: "OpenAI APIキーを変更" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /詳細設定/ }));
    expect(screen.queryByLabelText("OpenAI APIキー")).not.toBeInTheDocument();
    expect(await screen.findByLabelText("OpenAIモデル")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(
      await screen.findByText(
        "その他の設定は保存済み。AI機能の割り当てのみ保存できませんでした",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("設定を閉じる"));
    expect(
      screen.getByRole("dialog", { name: "変更を破棄しますか？" }),
    ).toBeInTheDocument();
  });
  it("edits ACP argv in Advanced and refreshes readiness after save", async () => {
    const reload = vi.fn().mockResolvedValue(undefined);
    const saveAssignments = vi.fn().mockResolvedValue(true);
    const savedCommand = [
      "python",
      "/opt/meeting supporter/acp_agent.py",
      "--stdio",
    ];
    sdkMocks.saveSettings.mockResolvedValueOnce({
      data: {
        ok: true,
        settings: settings({
          acp: {
            command: savedCommand,
            runtime: "acp",
            capabilities: ["reply"],
          },
        }),
      },
      error: undefined,
      request,
      response,
    });
    await renderModal(
      settings({
        acp: {
          command: ["old-agent"],
          runtime: "acp",
          capabilities: ["reply"],
        },
      }),
      routeCatalog({
        routes: [
          route(),
          route({
            id: "acp",
            kind: "local",
            label: "ACP",
            readiness: "ready",
            message: "ACP command is configured",
          }),
        ],
        reload,
        saveAssignments,
      }),
    );

    const advanced = screen.getByRole("button", {
      name: "詳細設定 外部・ローカル連携",
    });
    fireEvent.click(advanced);
    await waitFor(() =>
      expect(advanced).toHaveAttribute("aria-current", "page"),
    );
    const command = screen.getByRole("textbox", { name: "起動command" });
    expect(command).toHaveValue("old-agent");
    expect(screen.getByText("ACP / stdio")).toBeInTheDocument();
    expect(screen.getByText("返答案生成")).toBeInTheDocument();
    expect(
      screen.getByText("起動command設定済み（前回保存時）"),
    ).toBeInTheDocument();

    fireEvent.change(command, { target: { value: savedCommand.join("\n") } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(sdkMocks.saveSettings).toHaveBeenCalledOnce());
    expect(sdkMocks.saveSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({
          acp: { command: savedCommand },
        }),
      }),
    );
    expect(reload).toHaveBeenCalledOnce();
    expect(saveAssignments).toHaveBeenCalledOnce();
    expect(reload.mock.invocationCallOrder[0]).toBeLessThan(
      saveAssignments.mock.invocationCallOrder[0]!,
    );
  });

  it("locks a managed billing route while managed STT is active without locking another route", async () => {
    const managedRoute = route({
      id: "managed",
      kind: "managed",
      label: "Managed",
      description: "Managed service",
      readiness: "setup_required",
      selectable: false,
      selected: false,
      action: "manage_billing",
    });
    const codexRoute = route({
      readiness: "setup_required",
      action: "retry",
    });
    const reload = vi.fn();
    const routes = routeCatalog({
      routes: [managedRoute, codexRoute],
      reload,
    });

    await renderModal(
      settings({ stt: { backend: "managed", language: "ja" } }),
      routes,
      vi.fn(),
      true,
    );

    const managedAction = screen.getByRole("button", {
      name: "支払いを確認",
    });
    expect(managedAction).toBeDisabled();
    expect(screen.getByText("Managed service")).toBeInTheDocument();

    fireEvent.click(managedAction);
    expect(reload).not.toHaveBeenCalled();

    const unrelatedAction = screen.getByRole("button", {
      name: "もう一度確認",
    });
    expect(unrelatedAction).toBeEnabled();
    fireEvent.click(unrelatedAction);
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
  });

  it("leaves managed route actions available when another STT backend is active", async () => {
    const managedRoute = route({
      id: "managed",
      kind: "managed",
      label: "Managed",
      description: "Managed service",
      readiness: "setup_required",
      selectable: false,
      selected: false,
      action: "subscribe",
    });

    await renderModal(
      settings({ stt: { backend: "whisper", language: "ja" } }),
      routeCatalog({ routes: [managedRoute] }),
      vi.fn(),
      true,
    );

    expect(
      screen.getByRole("button", { name: "月額プランを申し込む" }),
    ).toBeEnabled();
  });

  it("restores stale STT and active-provider drafts when the meeting lock engages", async () => {
    const initial = settings({
      stt: {
        backend: "openai",
        language: "ja",
        openai_model: "whisper-1",
      },
      secrets: { OPENAI_API_KEY: true },
    });
    const routes = routeCatalog();
    const onClose = vi.fn();
    const rendered = await renderModal(initial, routes, onClose);

    fireEvent.click(screen.getByRole("button", { name: /音声/ }));
    fireEvent.change(await screen.findByLabelText("会議の言語"), {
      target: { value: "en" },
    });
    fireEvent.click(
      await screen.findByRole("button", {
        name: "OpenAI APIキーを変更",
      }),
    );
    fireEvent.change(screen.getByLabelText("OpenAI APIキー"), {
      target: { value: "stale-active-provider-key" },
    });

    fireEvent.click(screen.getByRole("button", { name: /詳細設定/ }));
    fireEvent.change(await screen.findByLabelText("OpenAIモデル"), {
      target: { value: "gpt-4o-mini-transcribe" },
    });

    fireEvent.click(screen.getByRole("button", { name: /データとプライバシー/ }));
    fireEvent.change(
      await screen.findByLabelText("録音の最大合計容量（MB）"),
      { target: { value: "64" } },
    );

    rendered.rerender(
      <SettingsModal
        onClose={onClose}
        routes={routes}
        audioSettingsLocked
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /音声/ }));
    await waitFor(() =>
      expect(screen.getByLabelText("会議の言語")).toHaveValue("ja"),
    );
    expect(screen.queryByLabelText("OpenAI APIキー")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "OpenAI APIキーを変更" }),
    ).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /詳細設定/ }));
    expect(await screen.findByLabelText("OpenAIモデル")).toHaveValue(
      "whisper-1",
    );

    fireEvent.click(screen.getByRole("button", { name: /データとプライバシー/ }));
    expect(
      await screen.findByLabelText("録音の最大合計容量（MB）"),
    ).toHaveValue(64);
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(sdkMocks.saveSettings).toHaveBeenCalledOnce());
    const body = sdkMocks.saveSettings.mock.calls[0]![0].body;
    expect(body).not.toHaveProperty("stt");
    expect(body).not.toHaveProperty("secrets");
    expect(body.recording_retention).toEqual({
      cutoff_date: null,
      max_total_bytes: 64 * 1024 * 1024,
    });
  });

  it("cancels an active STT credential deletion when the lock engages", async () => {
    const initial = settings({
      stt: { backend: "openai", language: "ja" },
      secrets: { OPENAI_API_KEY: true },
    });
    const routes = routeCatalog();
    const onClose = vi.fn();
    const rendered = await renderModal(initial, routes, onClose);

    fireEvent.click(screen.getByRole("button", { name: /音声/ }));
    fireEvent.click(
      await screen.findByRole("button", {
        name: "OpenAI APIキーを削除",
      }),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "OpenAI APIキーを削除予定にする",
      }),
    );
    expect(
      screen.getByRole("button", {
        name: "OpenAI APIキーの削除を取り消す",
      }),
    ).toBeInTheDocument();

    rendered.rerender(
      <SettingsModal
        onClose={onClose}
        routes={routes}
        audioSettingsLocked
      />,
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", {
          name: "OpenAI APIキーの削除を取り消す",
        }),
      ).not.toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(sdkMocks.saveSettings).toHaveBeenCalledOnce());
    const body = sdkMocks.saveSettings.mock.calls[0]![0].body;
    expect(body).not.toHaveProperty("delete_secrets");
    expect(body).not.toHaveProperty("stt");
  });

  it("saves unrelated settings while locked speech status is pending", async () => {
    const pendingSpeechStatus = new Promise<never>(() => {
      // Intentionally unresolved to keep the speech-model status check pending.
    });
    sdkMocks.getSpeechStatus.mockReturnValue(pendingSpeechStatus);
    await renderModal(settings(), routeCatalog(), vi.fn(), true);
    await waitFor(() => expect(sdkMocks.getSpeechStatus).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: /データとプライバシー/ }));
    fireEvent.change(
      await screen.findByLabelText("録音の最大合計容量（MB）"),
      { target: { value: "32" } },
    );
    const saveButton = screen.getByRole("button", { name: "保存" });
    expect(saveButton).toBeEnabled();
    fireEvent.click(saveButton);

    await waitFor(() => expect(sdkMocks.saveSettings).toHaveBeenCalledOnce());
    const body = sdkMocks.saveSettings.mock.calls[0]![0].body;
    expect(body).not.toHaveProperty("stt");
    expect(body.recording_retention).toEqual({
      cutoff_date: null,
      max_total_bytes: 32 * 1024 * 1024,
    });
  });

  it("loads each license only when its disclosure is opened", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response("PROVISIONED COMPONENTS\nuv 0.11.7", { status: 200 }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await renderModal();

    fireEvent.click(screen.getByRole("button", { name: /このアプリ/ }));
    const applicationDisclosure = await screen.findByText(
      "アプリケーションライセンス（AGPL-3.0-only）",
    );
    expect(fetchMock).not.toHaveBeenCalled();
    expect(licenseMocks.read).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "保存" }),
    ).not.toBeInTheDocument();

    fireEvent.click(applicationDisclosure);
    await waitFor(() =>
      expect(screen.getByTestId("application-license")).toHaveTextContent(
        "GNU AFFERO GENERAL PUBLIC LICENSE",
      ),
    );
    expect(licenseMocks.read).toHaveBeenCalledOnce();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("THIRD-PARTY-NOTICESを表示"));
    await waitFor(() =>
      expect(screen.getByTestId("third-party-notices")).toHaveTextContent(
        "PROVISIONED COMPONENTS",
      ),
    );
    expect(screen.getByTestId("third-party-notices")).toHaveTextContent(
      "uv 0.11.7",
    );
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("keeps About usable when the application license cannot be loaded", async () => {
    licenseMocks.read.mockImplementationOnce(() => {
      throw new Error("module load failed");
    });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await renderModal();
    fireEvent.click(screen.getByRole("button", { name: /このアプリ/ }));
    const applicationDisclosure = await screen.findByText(
      "アプリケーションライセンス（AGPL-3.0-only）",
    );

    fireEvent.click(applicationDisclosure);

    expect(
      await screen.findByText(
        "アプリケーションライセンスを読み込めませんでした。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("THIRD-PARTY-NOTICESを表示")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "このアプリについて" }),
    ).toBeInTheDocument();
    expect(licenseMocks.read).toHaveBeenCalledOnce();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
