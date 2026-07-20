import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MainMeetingControlScreen } from "./MainMeetingControlScreen";
import type { SendFn, SocketState, Turn } from "../types";
import type { AiUseCaseRouteStatus } from "../hooks/useAiRoutes";

function meetingState(overrides: Partial<SocketState> = {}): SocketState {
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
    devices: [
      { index: 1, name: "会議室スピーカー", is_monitor: true },
      { index: 2, name: "卓上マイク", is_monitor: false },
    ],
    deviceOther: 1,
    deviceSelf: 2,
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
    levelOther: 0.1,
    levelSelf: 0.05,
    ...overrides,
  };
}

function activeSession(turns: Turn[]): NonNullable<SocketState["session"]> {
  return {
    id: "active-meeting",
    startedAt: "2026-07-10T09:00:00.000Z",
    isActive: true,
    turns,
    aiNote: "",
  };
}

const READY_INFO_ROUTE: AiUseCaseRouteStatus = {
  readiness: "ready",
  canGenerate: true,
  message: null,
};

describe("MainMeetingControlScreen", () => {
  it("keeps audio health compact and exposes detailed input meters on demand", () => {
    render(
      <MainMeetingControlScreen
        state={meetingState()}
        send={vi.fn<SendFn>()}
        onSettings={() => {}}
      />,
    );

    expect(
      screen.getByRole("main", { name: "会話ワークスペース" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getByLabelText(/経過時間/)).toBeInTheDocument();
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "音声 正常" }));

    expect(
      screen.getByRole("meter", { name: "相手側の音声の入力レベル" }),
    ).toHaveAttribute("aria-valuenow", "35");
    expect(
      screen.getByRole("meter", { name: "自分のマイクの入力レベル" }),
    ).toHaveAttribute("aria-valuenow", "18");
  });

  it("treats role-specific default devices as healthy during an active meeting", () => {
    render(
      <MainMeetingControlScreen
        state={meetingState({
          deviceOther: null,
          deviceSelf: null,
          devices: [
            {
              index: "default-speaker",
              name: "内蔵スピーカー",
              is_monitor: true,
              is_default: true,
            },
            {
              index: "default-mic",
              name: "内蔵マイク",
              is_monitor: false,
              is_default: true,
            },
          ],
        })}
        send={vi.fn<SendFn>()}
        onSettings={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "音声 正常" }));
    expect(screen.getByText("既定スピーカー（内蔵スピーカー）")).toBeVisible();
    expect(screen.getByText("既定マイク（内蔵マイク）")).toBeVisible();
  });

  it("does not require prewarmed STT for an active cloud meeting", () => {
    render(
      <MainMeetingControlScreen
        state={meetingState({ sttInitialized: false })}
        send={vi.fn<SendFn>()}
        onSettings={() => {}}
      />,
    );

    expect(
      screen.getByRole("button", { name: "音声 正常" }),
    ).toBeInTheDocument();
  });
  it("reports audio as unavailable while the meeting socket is disconnected", () => {
    render(
      <MainMeetingControlScreen
        state={meetingState({ connected: false })}
        send={vi.fn<SendFn>()}
        onSettings={() => {}}
      />,
    );

    expect(
      screen.getByRole("button", { name: "音声 要確認" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "音声 正常" }),
    ).not.toBeInTheDocument();
  });

  it("requires confirmation before stopping the meeting", () => {
    const send = vi.fn<SendFn>();
    render(
      <MainMeetingControlScreen
        state={meetingState()}
        send={send}
        onSettings={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "会議を終了" }));
    expect(screen.getByText("この会議を終了しますか？")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "続ける" }));
    expect(send).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "会議を終了" }));
    fireEvent.click(screen.getByRole("button", { name: "終了する" }));
    expect(send).toHaveBeenCalledWith({ type: "stop_meeting" });
  });

  it("shows an empty conversation history without requiring another action", () => {
    render(
      <MainMeetingControlScreen
        state={meetingState()}
        send={vi.fn<SendFn>()}
        onSettings={() => {}}
      />,
    );

    const history = screen.getByRole("region", { name: "会話履歴の内容" });
    expect(history).toBeVisible();
    expect(history).toHaveTextContent("発言を待っています");
    expect(history).toHaveAttribute("id", "meeting-conversation-history");
  });

  it("renders confirmed turns in arrival order with their speakers", () => {
    render(
      <MainMeetingControlScreen
        state={meetingState({
          session: activeSession([
            { id: "other-first", speaker: "other", text: "予算を確認します。" },
            { id: "self-second", speaker: "self", text: "資料を共有します。" },
            {
              id: "other-third",
              speaker: "other",
              text: "ありがとうございます。",
            },
          ]),
        })}
        send={vi.fn<SendFn>()}
        onSettings={() => {}}
      />,
    );

    const history = screen.getByRole("region", { name: "会話履歴の内容" });
    expect(
      screen.getAllByRole("article").map((article) => article.textContent),
    ).toEqual([
      "相手予算を確認します。",
      "自分資料を共有します。",
      "相手ありがとうございます。",
    ]);
    expect(history).toHaveAttribute("id", "meeting-conversation-history");
  });

  it("appends live interim recognition after confirmed turns and announces it politely", () => {
    render(
      <MainMeetingControlScreen
        state={meetingState({
          session: activeSession([
            { id: "confirmed", speaker: "self", text: "次の議題です。" },
          ]),
          interimOther: "次の予定は",
          interimSelf: "午後なら",
        })}
        send={vi.fn<SendFn>()}
        onSettings={() => {}}
      />,
    );

    const entries = screen.getAllByRole("article");
    expect(entries.map((entry) => entry.textContent)).toEqual([
      "自分次の議題です。",
      "相手・聞き取り中次の予定は",
      "自分・聞き取り中午後なら",
    ]);
    expect(entries[1]).toHaveAttribute("aria-live", "polite");
    expect(entries[2]).toHaveAttribute("aria-live", "polite");
  });

  it("keeps a long open history scrolled to its newest confirmed turn", () => {
    const turns = Array.from(
      { length: 20 },
      (_, index): Turn => ({
        id: `turn-${index}`,
        speaker: index % 2 === 0 ? "other" : "self",
        text: `発言 ${index + 1}`,
      }),
    );
    const { rerender } = render(
      <MainMeetingControlScreen
        state={meetingState({ session: activeSession(turns) })}
        send={vi.fn<SendFn>()}
        onSettings={() => {}}
      />,
    );
    const history = screen.getByRole("region", { name: "会話履歴の内容" });
    Object.defineProperties(history, {
      clientHeight: { configurable: true, value: 192 },
      scrollHeight: { configurable: true, value: 960 },
    });
    history.scrollTop = 0;

    rerender(
      <MainMeetingControlScreen
        state={meetingState({
          session: activeSession([
            ...turns,
            { id: "newest", speaker: "self", text: "最後の発言です。" },
          ]),
        })}
        send={vi.fn<SendFn>()}
        onSettings={() => {}}
      />,
    );

    expect(history.scrollTop).toBe(960);
  });

  it("pins a historical reply under its target without replacing the current reply", () => {
    const session = activeSession([
      { id: "turn-old", speaker: "other", text: "以前の質問です。" },
      { id: "turn-current", speaker: "other", text: "現在の質問です。" },
    ]);
    render(
      <MainMeetingControlScreen
        state={meetingState({
          session,
          activeSuggestionTargetId: "turn-current",
          activeSuggestionGenerationId: "generation-current",
          replyText: "現在の返答案です。",
          suggestionCards: [
            {
              generationId: "generation-old",
              suggestionId: "suggestion-old",
              agentId: "reply",
              agentLabel: "標準",
              agentPriority: 1,
              targetUtteranceId: "turn-old",
              targetRole: "other",
              mode: "normal",
              text: "以前の返答案です。",
              status: "ready",
            },
          ],
        })}
        send={vi.fn<SendFn>()}
        onSettings={() => {}}
        replyReadiness="ready"
      />,
    );

    const historyReply = screen.getByRole("button", {
      name: /以前の質問です。/,
    });
    expect(historyReply).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(historyReply);

    expect(historyReply).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("以前の返答案です。")).toBeInTheDocument();
    expect(screen.getByText("現在の返答案です。")).toBeInTheDocument();
  });

  it("renders stable AI-note sections and supports an immediate refresh", () => {
    const send = vi.fn<SendFn>();
    render(
      <MainMeetingControlScreen
        state={meetingState({
          session: {
            ...activeSession([
              {
                id: "note-source",
                speaker: "other",
                text: "火曜日に共有します。",
              },
            ]),
            aiNote:
              "## 決まったこと\n- 火曜日に共有\n\n## 未確認・懸念\n- 適用日は未確認\n\n## 次にすること\n- 法務へ確認",
          },
        })}
        send={send}
        onSettings={() => {}}
        infoRouteStatus={READY_INFO_ROUTE}
      />,
    );

    expect(screen.getByText("決まったこと")).toBeInTheDocument();
    expect(screen.getByText("- 火曜日に共有")).toBeInTheDocument();
    expect(screen.getByText("未確認・懸念")).toBeInTheDocument();
    expect(screen.getByText("- 適用日は未確認")).toBeInTheDocument();
    expect(screen.getByText("次にすること")).toBeInTheDocument();
    expect(screen.getByText("- 法務へ確認")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "今すぐ整理" }));
    expect(send).toHaveBeenCalledWith({ type: "run_info" });
  });

  it("disables AI-note refresh until a confirmed turn exists", () => {
    const send = vi.fn<SendFn>();
    render(
      <MainMeetingControlScreen
        state={meetingState({ session: activeSession([]) })}
        send={send}
        onSettings={() => {}}
        infoRouteStatus={READY_INFO_ROUTE}
      />,
    );

    const refresh = screen.getByRole("button", { name: "今すぐ整理" });
    expect(refresh).toBeDisabled();
    fireEvent.click(refresh);
    expect(send).not.toHaveBeenCalled();
  });

  it.each([
    {
      name: "unassigned",
      status: {
        readiness: "setup_required",
        canGenerate: false,
        message: "会話メモを利用する支援方法を設定してください。",
      } satisfies AiUseCaseRouteStatus,
    },
    {
      name: "not ready",
      status: {
        readiness: "setup_required",
        canGenerate: false,
        message: "Codexへログインしてください。",
      } satisfies AiUseCaseRouteStatus,
    },
    {
      name: "capability mismatch",
      status: {
        readiness: "unavailable",
        canGenerate: false,
        message: "選択した支援方法では会話メモを利用できません。",
      } satisfies AiUseCaseRouteStatus,
    },
    {
      name: "catalog error",
      status: {
        readiness: "error",
        canGenerate: false,
        message:
          "支援方法の状態を確認できませんでした。しばらくしてから再度お試しください。",
      } satisfies AiUseCaseRouteStatus,
    },
  ])(
    "blocks info requests for $name while preserving the note and offering settings",
    ({ status }) => {
      const send = vi.fn<SendFn>();
      const onSettings = vi.fn();
      const session = {
        ...activeSession([
          { id: "note-source", speaker: "other" as const, text: "確認します。" },
        ]),
        aiNote: "## 決まったこと\n- 保存済みの内容",
      };
      render(
        <MainMeetingControlScreen
          state={meetingState({ session })}
          send={send}
          onSettings={onSettings}
          infoRouteStatus={status}
        />,
      );

      expect(screen.getByText("- 保存済みの内容")).toBeInTheDocument();
      expect(screen.getByText(status.message!)).toBeInTheDocument();
      const run = screen.getByRole("button", { name: "今すぐ整理" });
      expect(run).toBeDisabled();
      fireEvent.click(run);
      expect(send).not.toHaveBeenCalledWith({ type: "run_info" });
      fireEvent.click(screen.getByRole("button", { name: "設定を確認" }));
      expect(onSettings).toHaveBeenCalledOnce();
    },
  );

  it("distinguishes route loading from the advanced info kill switch", () => {
    const session = activeSession([
      { id: "note-source", speaker: "other", text: "確認します。" },
    ]);
    const { rerender } = render(
      <MainMeetingControlScreen
        state={meetingState({ session })}
        send={vi.fn<SendFn>()}
        onSettings={vi.fn()}
        infoRouteStatus={{
          readiness: "unknown",
          canGenerate: false,
          message: null,
        }}
      />,
    );

    expect(screen.getByText("会話メモの支援方法を確認しています。")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "設定を確認" }),
    ).not.toBeInTheDocument();

    rerender(
      <MainMeetingControlScreen
        state={meetingState({
          session,
          agentSettings: {
            replyEnabled: true,
            replyAutoGenerate: false,
            replyAgents: [],
            infoEnabled: false,
          },
        })}
        send={vi.fn<SendFn>()}
        onSettings={vi.fn()}
        infoRouteStatus={READY_INFO_ROUTE}
      />,
    );

    expect(
      screen.getByText("設定ファイルで会話メモAIが無効になっています。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "設定を確認" }),
    ).not.toBeInTheDocument();
  });
});
