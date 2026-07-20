import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LiveReplySidePanel } from "./LiveReplySidePanel";
import type { SendFn, SocketState } from "../../types";
import type { AiRouteReadModel } from "../../hooks/useAiRoutes";

const hideCurrentWindowMock = vi.hoisted(() =>
  vi.fn<() => Promise<void>>(async () => {}),
);
const useAiRoutesMock = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/window", () => ({
  Window: class MockWindow {
    static async getByLabel() {
      return null;
    }
  },
  getCurrentWindow: () => ({
    setAlwaysOnTop: vi.fn(async () => {}),
    isAlwaysOnTop: vi.fn(async () => true),
  }),
}));

vi.mock("@tauri-apps/api/core", () => ({
  isTauri: () => true,
}));

vi.mock("../../platform/tauriWindow", async () => {
  const actual = await vi.importActual<
    typeof import("../../platform/tauriWindow")
  >("../../platform/tauriWindow");
  return {
    ...actual,
    hideCurrentWindow: hideCurrentWindowMock,
  };
});
vi.mock("../../hooks/useAlwaysOnTop", () => ({
  ASSISTANT_ALWAYS_ON_TOP_KEY: "meeting-supporter.assistant-always-on-top",
  useAlwaysOnTop: () => ({
    actual: "on",
    busy: false,
    issue: null,
    statusMessage: null,
    toggle: vi.fn(async () => {}),
    retry: vi.fn(async () => {}),
  }),
}));

vi.mock("../../hooks/useAiRoutes", () => ({
  useAiRoutes: useAiRoutesMock,
}));

function createState(overrides: Partial<SocketState> = {}): SocketState {
  return {
    connected: true,
    statusText: "接続済み",
    isRunning: true,
    sttBackend: "google",
    sttInitialized: true,
    sttInitializing: false,
    sttInitRequested: false,
    agentSettings: {
      replyEnabled: true,
      replyAutoGenerate: false,
      replyAgents: [],
      infoEnabled: true,
    },
    devices: [],
    deviceOther: null,
    deviceSelf: null,
    session: null,
    activeSuggestionTargetId: null,
    activeSuggestionGenerationId: null,
    suggestionCards: [],
    replyText: "",
    isGeneratingReply: false,
    lastReplyCancelResult: null,
    cancelledSuggestionIds: [],
    discardedGenerationIds: [],
    isResearchingInfo: false,
    interimOther: "",
    interimSelf: "",
    levelOther: 0,
    levelSelf: 0,
    ...overrides,
  };
}

const readyCodexRoute: AiRouteReadModel = {
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
};

function routeCatalog(overrides: Record<string, unknown> = {}) {
  return {
    routes: [readyCodexRoute],
    assignments: { reply: "codex", info: null, minutes: null },
    assignedRoutes: { reply: readyCodexRoute, info: null, minutes: null },
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
    setDraftAssignment: vi.fn(),
    resetDraftAssignments: vi.fn(),
    loading: false,
    saving: false,
    error: null,
    manualReloadStatus: "idle",
    reload: vi.fn(),
    saveAssignments: vi.fn(),
    ...overrides,
  };
}

describe("LiveReplySidePanel", () => {
  beforeEach(() => {
    hideCurrentWindowMock.mockClear();
    useAiRoutesMock.mockReturnValue(routeCatalog());
  });

  it("返答生成ボタンで generate_reply を送信する", () => {
    const send = vi.fn<SendFn>();

    render(<LiveReplySidePanel state={createState()} send={send} />);

    fireEvent.click(screen.getByRole("button", { name: "返答案を作る" }));

    expect(send).toHaveBeenCalledWith({
      type: "generate_reply",
      generation_id: expect.any(String),
    });
  });

  it("does not generate when the active route is ready but not selectable", () => {
    const nonSelectableRoute = { ...readyCodexRoute, selectable: false };
    useAiRoutesMock.mockReturnValue(
      routeCatalog({
        assignedRoutes: {
          reply: nonSelectableRoute,
          info: null,
          minutes: null,
        },
        replyStatus: {
          readiness: "unavailable",
          canGenerate: false,
          message: "選択した支援方法では返答案を利用できません。",
        },
      }),
    );

    render(<LiveReplySidePanel state={createState()} send={vi.fn<SendFn>()} />);

    expect(screen.getByRole("button", { name: "返答案を作る" })).toBeDisabled();
  });

  it("keeps the completed cue visible through newer transcription, then switches only after a new request starts", () => {
    const send = vi.fn<SendFn>();
    const session = {
      id: "session-1",
      startedAt: "2026-07-10T00:00:00.000Z",
      isActive: true,
      turns: [
        {
          id: "turn-visible",
          speaker: "other" as const,
          text: "前の質問です。",
        },
        {
          id: "turn-next-other",
          speaker: "other" as const,
          text: "次の相手の発言です。",
        },
        {
          id: "turn-next",
          speaker: "self" as const,
          text: "次の自分の発言です。",
        },
      ],
      aiNote: "",
    };
    const completedCard: SocketState["suggestionCards"][number] = {
      generationId: "generation-visible",
      suggestionId: "suggestion-visible",
      agentId: "reply",
      agentLabel: "標準",
      agentPriority: 10,
      targetUtteranceId: "turn-visible",
      targetRole: "other",
      mode: "normal",
      text: "表示を維持する完成案です。",
      status: "ready",
      errorText: null,
    };
    const nextPendingCard: SocketState["suggestionCards"][number] = {
      ...completedCard,
      suggestionId: "suggestion-next",
      generationId: "generation-next",
      targetUtteranceId: "turn-next",
      targetRole: "self",
      text: "",
      status: "generating",
    };

    const { rerender } = render(
      <LiveReplySidePanel
        state={createState({
          session,
          activeSuggestionTargetId: "turn-visible",
          suggestionCards: [completedCard],
          replyText: completedCard.text,
          interimOther: "さらに聞き取り中です。",
        })}
        send={send}
      />,
    );

    expect(screen.getByText(completedCard.text)).toBeInTheDocument();
    expect(screen.getByText("さらに聞き取り中です。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返答案を作る" }));
    expect(send).toHaveBeenCalledWith({
      type: "generate_reply",
      generation_id: expect.any(String),
      target_utterance_id: "turn-next",
    });

    rerender(
      <LiveReplySidePanel
        state={createState({
          session,
          activeSuggestionTargetId: "turn-next",
          suggestionCards: [completedCard, nextPendingCard],
          replyText: "",
          isGeneratingReply: true,
        })}
        send={send}
      />,
    );

    expect(screen.getByLabelText("返答案を作成中")).toBeInTheDocument();
    expect(screen.queryByText(completedCard.text)).not.toBeInTheDocument();

    const nextCompletedCard = {
      ...nextPendingCard,
      text: "新しい返答案を作成しました。",
      status: "ready" as const,
    };
    rerender(
      <LiveReplySidePanel
        state={createState({
          session,
          activeSuggestionTargetId: "turn-next",
          suggestionCards: [completedCard, nextCompletedCard],
          replyText: nextCompletedCard.text,
          isGeneratingReply: false,
        })}
        send={send}
      />,
    );

    expect(screen.getByText(nextCompletedCard.text)).toBeInTheDocument();
    expect(screen.queryByText(completedCard.text)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("返答案を作成中")).not.toBeInTheDocument();
  });

  it("返答がある場合はコピー操作の状態を表示する", async () => {
    const writeClipboard = vi.fn(async (_text: string): Promise<void> => {});

    render(
      <LiveReplySidePanel
        state={createState({ replyText: "この内容をコピーします。" })}
        send={vi.fn<SendFn>()}
        writeClipboard={writeClipboard}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "コピー" }));

    await waitFor(() =>
      expect(writeClipboard).toHaveBeenCalledWith("この内容をコピーします。"),
    );
    expect(
      screen.getByRole("button", { name: "コピーしました" }),
    ).toBeInTheDocument();
  });

  it("blocks generation until a reply route is assigned and ready", () => {
    useAiRoutesMock.mockReturnValue(
      routeCatalog({
        routes: [],
        assignments: { reply: null, info: null, minutes: null },
        assignedRoutes: { reply: null, info: null, minutes: null },
        draftAssignments: { reply: null, info: null, minutes: null },
        replyStatus: {
          readiness: "setup_required",
          canGenerate: false,
          message: "返答案を利用する支援方法を設定してください。",
        },
      }),
    );

    render(<LiveReplySidePanel state={createState()} send={vi.fn<SendFn>()} />);

    expect(
      screen.getByText("返答支援を使うには準備が必要です。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返答案を作る" })).toBeDisabled();
  });

  it("surfaces a suggestion failure and retries the same latest utterance", () => {
    const send = vi.fn<SendFn>();
    render(
      <LiveReplySidePanel
        state={createState({
          session: {
            id: "session-1",
            startedAt: "2026-07-06T00:00:00.000Z",
            isActive: true,
            turns: [
              {
                id: "turn-1",
                speaker: "other",
                text: "次の候補はありますか？",
              },
            ],
            aiNote: "",
          },
          activeSuggestionTargetId: "turn-1",
          activeSuggestionGenerationId: "generation-failed",
          suggestionCards: [
            {
              generationId: "generation-failed",
              suggestionId: "failed-suggestion",
              agentId: "reply",
              agentLabel: "標準",
              agentPriority: 10,
              targetUtteranceId: "turn-1",
              targetRole: "other",
              mode: "normal",
              text: "",
              status: "error",
              errorText: "runtime unavailable",
            },
          ],
        })}
        send={send}
      />,
    );

    expect(screen.getByText("返答案を作れませんでした")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "再試行" }));
    expect(send).toHaveBeenCalledWith({
      type: "generate_reply",
      generation_id: expect.any(String),
      target_utterance_id: "turn-1",
    });
  });

  it("stops the active generation and reports the authoritative applied result", async () => {
    const send = vi.fn<SendFn>();
    const generatingCard: SocketState["suggestionCards"][number] = {
      generationId: "generation-stop",
      suggestionId: "suggestion-stop",
      agentId: "reply",
      agentLabel: "標準",
      agentPriority: 10,
      targetUtteranceId: "turn-stop",
      targetRole: "other",
      mode: "normal",
      text: "生成途中",
      status: "generating",
      errorText: null,
    };
    const state = createState({
      session: {
        id: "session-stop",
        startedAt: "2026-07-15T00:00:00.000Z",
        isActive: true,
        turns: [{ id: "turn-stop", speaker: "other", text: "止めてください" }],
        aiNote: "",
      },
      activeSuggestionTargetId: "turn-stop",
      activeSuggestionGenerationId: "generation-stop",
      suggestionCards: [generatingCard],
      replyText: "生成途中",
      isGeneratingReply: true,
    });
    const { rerender } = render(
      <LiveReplySidePanel state={state} send={send} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "停止" }));

    expect(send).toHaveBeenCalledWith({
      type: "cancel_reply",
      generation_id: "generation-stop",
      target_utterance_id: "turn-stop",
    });
    expect(
      screen.getByRole("button", { name: "停止結果を確認中" }),
    ).toBeDisabled();

    rerender(
      <LiveReplySidePanel
        state={createState({
          ...state,
          suggestionCards: [{ ...generatingCard, status: "cancelled" }],
          replyText: "",
          isGeneratingReply: false,
          lastReplyCancelResult: {
            generationId: "generation-stop",
            targetUtteranceId: "turn-stop",
            status: "applied",
            cancelledSuggestionIds: ["suggestion-stop"],
          },
        })}
        send={send}
      />,
    );

    expect(
      await screen.findByText("返答案の生成を停止しました。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返答案を作る" })).toBeEnabled();
  });

  it("reports not-applied cancellation without claiming success", async () => {
    const send = vi.fn<SendFn>();
    const generatingCard: SocketState["suggestionCards"][number] = {
      generationId: "generation-complete",
      suggestionId: "suggestion-complete",
      agentId: "reply",
      agentLabel: "標準",
      agentPriority: 10,
      targetUtteranceId: "turn-complete",
      targetRole: "other",
      mode: "normal",
      text: "完成する返答案",
      status: "generating",
      errorText: null,
    };
    const state = createState({
      activeSuggestionTargetId: "turn-complete",
      activeSuggestionGenerationId: "generation-complete",
      suggestionCards: [generatingCard],
      replyText: generatingCard.text,
      isGeneratingReply: true,
    });
    const { rerender } = render(
      <LiveReplySidePanel state={state} send={send} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "停止" }));

    rerender(
      <LiveReplySidePanel
        state={createState({
          ...state,
          suggestionCards: [{ ...generatingCard, status: "ready" }],
          isGeneratingReply: false,
          lastReplyCancelResult: {
            generationId: "generation-complete",
            targetUtteranceId: "turn-complete",
            status: "not_applied",
            cancelledSuggestionIds: [],
          },
        })}
        send={send}
      />,
    );

    expect(
      await screen.findByText(
        "停止対象が見つからないか、返答案がすでに完了しています。",
      ),
    ).toBeInTheDocument();
  });

  it("discards the active ready generation through the store action", () => {
    const discard = vi.fn();
    render(
      <LiveReplySidePanel
        state={createState({
          activeSuggestionTargetId: "turn-ready",
          activeSuggestionGenerationId: "generation-ready",
          replyText: "破棄する返答案",
        })}
        send={vi.fn<SendFn>()}
        onDiscardReply={discard}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "破棄" }));
    expect(discard).toHaveBeenCalledOnce();
  });

  it("main 画面への設定導線は assistant window を hide する", () => {
    hideCurrentWindowMock.mockClear();

    render(
      <LiveReplySidePanel
        state={createState()}
        send={vi.fn<SendFn>()}
        replyReadiness="setup_required"
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "メイン画面で設定を確認" }),
    );

    expect(hideCurrentWindowMock).toHaveBeenCalledTimes(1);
  });

  it("native chrome の下には status と独立した pin control だけを置く", async () => {
    render(<LiveReplySidePanel state={createState()} send={vi.fn<SendFn>()} />);

    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByText("会議中")).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "常に前面表示を解除" }),
    ).toHaveAttribute("title", "常に前面 ON");
    expect(
      screen.queryByRole("button", { name: "支援ウィンドウを隠す" }),
    ).not.toBeInTheDocument();
  });
});
