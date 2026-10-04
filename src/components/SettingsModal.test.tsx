import { useRef, useState } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  saveSettingsApiSettingsPost,
  testConnectionApiSettingsConnectionsTestPost,
} from "../api/generated/sdk.gen";
import type { SpeechModelStatusResponse } from "../api/generated/types.gen";
import type {
  AiRouteReadModel,
  AiRoutesController,
} from "../hooks/useAiRoutes";
import { SettingsModal } from "./SettingsModal";
import { SettingsTaskNotice } from "./SettingsTaskNotice";
import { resetSpeechModelTasks } from "../store/speechModelStore";
import { useSettingsTaskStore } from "../store/settingsTaskStore";

const sdkMocks = vi.hoisted(() => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  testConnection: vi.fn(),
  getSpeechStatus: vi.fn(),
  startSpeechDownload: vi.fn(),
  cancelSpeechDownload: vi.fn(),
  getOllamaModels: vi.fn(),
  getAiModels: vi.fn(),
}));
vi.mock("./settings/AgentRegistryPanel", () => ({
  AgentRegistryPanel: () => null,
}));

vi.mock("../api/generated/sdk.gen", () => ({
  getSettingsApiSettingsGet: sdkMocks.getSettings,
  saveSettingsApiSettingsPost: sdkMocks.saveSettings,
  testConnectionApiSettingsConnectionsTestPost: sdkMocks.testConnection,
  getSpeechModelStatusApiSttModelGet: sdkMocks.getSpeechStatus,
  startSpeechModelDownloadApiSttModelDownloadPost: sdkMocks.startSpeechDownload,
  cancelSpeechModelDownloadApiSttModelCancelPost: sdkMocks.cancelSpeechDownload,
  getOllamaModelsApiSettingsOllamaModelsGet: sdkMocks.getOllamaModels,
  getAiModels: sdkMocks.getAiModels,
}));
vi.mock("../api/recordingRetention", () => ({
  previewRecordingCleanup: vi.fn(),
  executeRecordingCleanup: vi.fn(),
}));

const response = new Response(null, { status: 200 });
const request = new Request("http://localhost/api/settings");
// Raw responses also exercise legacy and unsupported backend values.
function settings(overrides: Record<string, unknown> = {}) {
  return {
    ollama: { base_url: "http://127.0.0.1:11434/v1" },
    stt: { backend: "whisper", language: "ja" },
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
    id: "ollama",
    kind: "local",
    label: "Ollama",
    description: "Local model",
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
    assignments: { reply: "ollama" },
    assignedRoutes: { reply: route() },
    replyStatus: { readiness: "ready", canGenerate: true, message: null },
    draftAssignments: { reply: "ollama" },
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
  await screen.findByRole("heading", { name: "AIと音声認識" });
  fireEvent.click(screen.getByText("返答案の動作"));
  return { ...rendered, onClose };
}
afterEach(() => {
  vi.unstubAllGlobals();
  resetSpeechModelTasks();
});

describe("SettingsModal connection UX", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetSpeechModelTasks();
    useSettingsTaskStore.setState({ tasks: {} });
    sdkMocks.getSpeechStatus.mockResolvedValue({
      data: speechStatus(),
      error: undefined,
      request,
      response,
    });
    sdkMocks.getOllamaModels.mockImplementation(async ({ query }) => ({
      data: {
        ok: true,
        base_url: query.base_url,
        models: ["qwen3", "synthetic-model:8b"],
        message: null,
      },
      error: undefined,
      request,
      response,
    }));
    sdkMocks.getAiModels.mockImplementation(async ({ query }) => ({
      data: {
        ok: true,
        provider: query.provider,
        models: [
          {
            id:
              query.provider === "openai"
                ? "gpt-5.4-mini"
                : query.provider === "gemini"
                  ? "gemini-3.1-flash-lite"
                  : "claude-haiku-4-5-20251001",
            label: "推奨モデル",
          },
        ],
        message: null,
      },
      error: undefined,
      request,
      response,
    }));
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

  it("shows the Rust backend's audio-settings conflict instead of a generic save failure", async () => {
    sdkMocks.saveSettings.mockResolvedValueOnce({
      data: undefined,
      error: {
        detail: {
          code: "AUDIO_SETTINGS_LOCKED",
          message: "会議中・準備中は音声認識の設定を変更できません。",
        },
      },
      request,
      response: new Response(null, { status: 409 }),
    });
    await renderModal(
      settings({ stt: { backend: "dummy", language: "ja" } }),
      routeCatalog(),
    );
    fireEvent.click(
      screen.getByRole("checkbox", { name: "発話ごとに自動で作る" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(
      await screen.findByText(
        "会議中・準備中は音声認識の設定を変更できません。",
      ),
    ).toBeInTheDocument();
  });

  it("keeps a model download alive across closing and reopening settings and reports completion outside the dialog", async () => {
    sdkMocks.getSettings.mockResolvedValue({
      data: settings(),
      error: undefined,
      request,
      response,
    });
    sdkMocks.getSpeechStatus.mockResolvedValue({
      data: { ...speechStatus(), state: "missing" },
      error: undefined,
    });
    let finishStart!: (value: {
      data: SpeechModelStatusResponse;
      error: undefined;
    }) => void;
    let startSignal: AbortSignal | undefined;
    sdkMocks.startSpeechDownload.mockImplementation(
      ({ signal }: { signal: AbortSignal }) => {
        startSignal = signal;
        return new Promise((resolve) => {
          finishStart = resolve;
        });
      },
    );
    const routes = routeCatalog();
    function Harness() {
      const [open, setOpen] = useState(true);
      return open ? (
        <SettingsModal onClose={() => setOpen(false)} routes={routes} />
      ) : (
        <SettingsTaskNotice onOpenSettings={() => setOpen(true)} />
      );
    }
    render(<Harness />);
    await screen.findByRole("heading", { name: "文字起こし" });
    fireEvent.click(
      await screen.findByRole("button", { name: "モデルをダウンロード" }),
    );
    fireEvent.click(screen.getByLabelText("設定を閉じる"));
    expect(screen.queryByTestId("settings-modal")).not.toBeInTheDocument();
    expect(startSignal?.aborted).toBe(false);
    expect(
      screen.getByText(/設定を閉じても処理は続きます/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "設定を開く" }));
    await screen.findByRole("heading", { name: "文字起こし" });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "モデルをダウンロード" }),
      ).toBeDisabled(),
    );
    expect(sdkMocks.startSpeechDownload).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByLabelText("設定を閉じる"));
    await act(async () => {
      finishStart({
        data: { ...speechStatus(), state: "downloading", progress_percent: 40 },
        error: undefined,
      });
    });
    expect(screen.getByText(/取得中 40%/)).toBeInTheDocument();
    sdkMocks.getSpeechStatus.mockResolvedValue({
      data: speechStatus(),
      error: undefined,
    });
    expect(await screen.findByText(/取得が完了しました/)).toBeInTheDocument();
    expect(sdkMocks.cancelSpeechDownload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "処理の通知を閉じる" }));
    expect(screen.queryByText(/取得が完了しました/)).not.toBeInTheDocument();
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

    await screen.findByRole("heading", { name: "AIと音声認識" });
    fireEvent.click(screen.getByText("返答案の動作"));
    expect(screen.getByRole("heading", { name: "設定" })).toHaveFocus();
    fireEvent.click(screen.getByLabelText("設定を閉じる"));
    await waitFor(() => expect(trigger).toHaveFocus());
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
          draftAssignments: { reply: provider },
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
      expect(
        testConnectionApiSettingsConnectionsTestPost,
      ).toHaveBeenCalledOnce();
      expect(saveSettingsApiSettingsPost).not.toHaveBeenCalled();
    },
  );

  it("schedules and cancels a saved key deletion before global save", async () => {
    const catalog = routeCatalog({
      routes: [
        route({ id: "openai", kind: "byok", label: "OpenAI API" }),
        route(),
      ],
      draftAssignments: { reply: "openai" },
    });
    const view = await renderModal(
      settings({
        stt: { backend: "dummy", language: "ja" },
        secrets: { OPENAI_API_KEY: true },
      }),
      catalog,
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
    view.rerender(
      <SettingsModal
        onClose={vi.fn()}
        routes={{ ...catalog, draftAssignments: { reply: "ollama" } }}
      />,
    );
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

  it("keeps the selected model and credentials together without a separate advanced category", async () => {
    await renderModal(
      settings({ stt: { backend: "dummy", language: "ja" } }),
      routeCatalog({
        routes: [route({ id: "openai", kind: "byok" })],
        draftAssignments: { reply: "openai" },
      }),
    );
    expect(screen.getByLabelText("OpenAI APIキー")).toBeVisible();
    expect(screen.getByLabelText("OpenAIモデル")).toHaveValue("gpt-5.4-mini");
    expect(
      screen.queryByRole("button", { name: /詳細設定/ }),
    ).not.toBeInTheDocument();
    await waitFor(() => expect(sdkMocks.getAiModels).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText("OpenAIモデル"), {
      target: { value: "__custom_model__" },
    });
    expect(screen.getByLabelText("OpenAIカスタムモデル識別子")).toBeVisible();
  });

  it("shows service choices with a single selected service and omits hosted accounts", async () => {
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
        ],
        draftAssignments: { reply: "openai" },
      }),
    );
    expect(screen.queryByText("アプリにおまかせ")).not.toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "返答案に使うAI" }),
    ).toHaveValue("openai");
    expect(screen.getByRole("option", { name: /Ollama/ })).toBeInTheDocument();
    expect(screen.queryByText("外部エージェント連携")).not.toBeInTheDocument();
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
        draftAssignments: { reply: "openai" },
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
        draftAssignments: { reply: "gemini" },
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
        draftAssignments: { reply: "openai" },
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
    expect(
      document.querySelector('[data-settings-page="AIと音声認識"]'),
    ).toHaveTextContent(
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

  it("validates the credential of the selected reply route", async () => {
    const openaiRoute = route({
      id: "openai",
      kind: "byok",
      label: "OpenAI API",
      capabilities: ["reply"],
    });
    const geminiRoute = route({
      id: "gemini",
      kind: "byok",
      label: "Gemini API",
      capabilities: ["reply"],
    });
    await renderModal(
      settings({
        stt: { backend: "dummy", language: "ja" },
        secrets: { OPENAI_API_KEY: true },
      }),
      routeCatalog({
        routes: [openaiRoute, geminiRoute],
        assignments: { reply: "gemini" },
        draftAssignments: {
          reply: "gemini",
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
        assignments: { reply: null },
        draftAssignments: { reply: null },
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

  it("reports partial route save success without discarding unsaved assignments", async () => {
    const routes = routeCatalog({
      assignmentDirty: true,
      saveAssignments: vi.fn().mockResolvedValue(false),
    });
    await renderModal(
      settings({
        stt: { backend: "dummy", language: "ja" },
        secrets: { OPENAI_API_KEY: true },
      }),
      routes,
    );
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
  it("shows the Ollama URL before the model selector and saves the selected model", async () => {
    await renderModal();
    const url = screen.getByRole("textbox", { name: "OllamaベースURL" });
    const model = screen.getByRole("combobox", { name: "Ollamaモデル" });
    expect(url).toBeVisible();
    expect(
      url.compareDocumentPosition(model) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    await screen.findByRole("option", { name: "synthetic-model:8b" });
    expect(model).toHaveValue("qwen3");

    fireEvent.change(model, { target: { value: "synthetic-model:8b" } });
    expect(sdkMocks.saveSettings).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(sdkMocks.saveSettings).toHaveBeenCalledOnce());
    expect(sdkMocks.saveSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({
          ollama: { base_url: "http://127.0.0.1:11434/v1" },
          ai_models: expect.objectContaining({ ollama: "synthetic-model:8b" }),
        }),
      }),
    );
  });

  it("keeps legacy agent commands out of Advanced settings", async () => {
    await renderModal();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /AIと音声認識/ }));
    });
    expect(
      screen.queryByRole("textbox", { name: "起動command" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("ACP（実験的機能）")).not.toBeInTheDocument();
    expect(screen.getByLabelText("OllamaベースURL")).toBeInTheDocument();
  });

  it("omits account navigation and billing actions even for an offered hosted route", async () => {
    await renderModal(
      settings(),
      routeCatalog({
        routes: [
          route({ id: "managed", kind: "managed", action: "subscribe" }),
        ],
        draftAssignments: { reply: "managed" },
      }),
    );
    expect(
      screen.queryByRole("button", { name: /アカウント|プラン|月額/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /おまかせ/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "選択していたAIは現在利用できません。別のAIを選んでください。",
      ),
    ).toBeVisible();
  });

  it("saves unrelated settings while locked speech status is pending", async () => {
    const pendingSpeechStatus = new Promise<never>(() => {
      // Intentionally unresolved to keep the speech-model status check pending.
    });
    sdkMocks.getSpeechStatus.mockReturnValue(pendingSpeechStatus);
    await renderModal(settings(), routeCatalog(), vi.fn(), true);
    await waitFor(() => expect(sdkMocks.getSpeechStatus).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: /データと保存/ }));
    fireEvent.change(await screen.findByLabelText("録音の最大合計容量（MB）"), {
      target: { value: "32" },
    });
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

  it("shows application and third-party license notices without a save action", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("PROVISIONED COMPONENTS\nuv 0.11.7", { status: 200 }),
        ),
    );

    await renderModal();

    fireEvent.click(screen.getByRole("button", { name: /このアプリ/ }));
    expect(
      screen.getByText("アプリケーションライセンス（AGPL-3.0-only）"),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByText("アプリケーションライセンス（AGPL-3.0-only）"),
    );
    expect(screen.getByTestId("application-license")).toHaveTextContent(
      "GNU AFFERO GENERAL PUBLIC LICENSE",
    );
    expect(screen.getByText("THIRD-PARTY-NOTICESを表示")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "保存" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("THIRD-PARTY-NOTICESを表示"));
    await waitFor(() =>
      expect(screen.getByTestId("third-party-notices")).toHaveTextContent(
        "PROVISIONED COMPONENTS",
      ),
    );
    expect(screen.getByTestId("third-party-notices")).toHaveTextContent(
      "uv 0.11.7",
    );
  });
  it.each(["unknown"])(
    "requires selection for an unsupported %s speech value from the API",
    async (backend) => {
      await renderModal(settings({ stt: { backend, language: "ja" } }));

      expect(screen.getByLabelText("音声認識方式")).toHaveValue("");
      expect(
        screen.getByText("音声認識方式を選択してください。"),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("textbox", { name: /APIキー/ }),
      ).not.toBeInTheDocument();
      expect(sdkMocks.testConnection).not.toHaveBeenCalled();
      expect(sdkMocks.saveSettings).not.toHaveBeenCalled();
      await act(async () => {
        fireEvent.change(screen.getByLabelText("音声認識方式"), {
          target: { value: "reazonspeech" },
        });
      });
      expect(screen.getByLabelText("音声認識方式")).toHaveValue("reazonspeech");
    },
  );

  it("offers only local speech choices", async () => {
    await renderModal();
    await act(async () => {});
    const select = screen.getByLabelText("音声認識方式");
    expect(
      Array.from(select.querySelectorAll("option"), (option) => option.value),
    ).toEqual(["whisper", "reazonspeech"]);
  });
});
