import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SetupScreen } from "./SetupScreen";
import type { SendFn, SocketState } from "../types";

const noopVoid = (): void => {};
const originalAudioContext = Object.getOwnPropertyDescriptor(
  window,
  "AudioContext",
);
const replyRouteProps = {
  showFirstRunGuidance: false,
  replyStatus: {
    readiness: "ready" as const,
    canGenerate: true,
    message: null,
  },
  replyReloadStatus: "idle" as const,
  onReloadReplyStatus: noopVoid,
};

function idleState(overrides: Partial<SocketState> = {}): SocketState {
  return {
    connected: true,
    statusText: "接続中",
    isRunning: false,
    sttBackend: "whisper",
    sttInitialized: false,
    sttInitializing: false,
    sttInitRequested: false,
    agentSettings: {
      replyEnabled: true,
      replyAutoGenerate: false,
      replyAgents: [],
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
    interimOther: "",
    interimSelf: "",
    levelOther: 0,
    levelSelf: 0,
    ...overrides,
  };
}

function installAudioContext(resume: Promise<void> = Promise.resolve()) {
  const oscillator = {
    type: "sine",
    frequency: {
      setValueAtTime: vi.fn(),
      linearRampToValueAtTime: vi.fn(),
    },
    connect: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
    onended: null as OscillatorNode["onended"],
  };
  const gain = {
    gain: {
      setValueAtTime: vi.fn(),
      linearRampToValueAtTime: vi.fn(),
    },
    connect: vi.fn(),
  };
  const context = {
    currentTime: 1,
    destination: {},
    createOscillator: vi.fn(() => oscillator),
    createGain: vi.fn(() => gain),
    resume: vi.fn(() => resume),
    close: vi.fn(() => Promise.resolve()),
  };
  const constructor = vi.fn(function AudioContextMock() {
    return context;
  });
  Object.defineProperty(window, "AudioContext", {
    configurable: true,
    value: constructor,
  });
  return { constructor, context, oscillator };
}

afterEach(() => {
  if (originalAudioContext) {
    Object.defineProperty(window, "AudioContext", originalAudioContext);
  } else {
    Reflect.deleteProperty(window, "AudioContext");
  }
});

describe("SetupScreen", () => {
  it.each([
    ["interrupted", "会議を中断しました", false],
    ["unsaved", "一部の記録を保存できませんでした", false],
    ["stop_failed", "音声処理の停止を確認できませんでした", true],
  ] as const)(
    "handles %s without a recovery dialog",
    (endStatus, title, blocked) => {
      const send = vi.fn<SendFn>();
      const onHistory = vi.fn();
      render(
        <SetupScreen
          {...replyRouteProps}
          showFirstRunGuidance={false}
          state={idleState({
            sttInitialized: true,
            meetingEndStatus: endStatus,
          })}
          send={send}
          onSettings={vi.fn()}
          onHistory={onHistory}
        />,
      );
      expect(screen.getByText(title)).toBeInTheDocument();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "履歴を確認" }));
      expect(onHistory).toHaveBeenCalledOnce();
      const start = screen.getByRole("button", { name: "会議を開始" });
      if (blocked) {
        expect(start).toBeDisabled();
        expect(
          screen.getByText(/アプリを再起動してください/),
        ).toBeInTheDocument();
      } else {
        expect(start).toBeEnabled();
        fireEvent.click(start);
        expect(send).toHaveBeenCalledWith(
          expect.objectContaining({ type: "start_meeting" }),
        );
      }
    },
  );

  it("keeps optional context collapsed and links directly to AI setup", () => {
    const onSettings = vi.fn();
    render(
      <SetupScreen
        {...replyRouteProps}
        showFirstRunGuidance
        state={idleState()}
        send={vi.fn()}
        onSettings={onSettings}
      />,
    );
    expect(screen.getByRole("heading", { name: "新しい会議" })).toBeVisible();
    expect(
      screen.getByText("会議の詳細・資料を追加").closest("details"),
    ).not.toHaveAttribute("open");
    fireEvent.click(screen.getByRole("button", { name: "AIを設定" }));
    expect(onSettings).toHaveBeenCalledOnce();
  });

  it("plays one system test sound at a time and reports completion", async () => {
    let resolveResume: () => void = noopVoid;
    const resume = new Promise<void>((resolve) => {
      resolveResume = resolve;
    });
    const audio = installAudioContext(resume);
    render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({ sttBackend: "google" })}
        send={vi.fn<SendFn>()}
        onSettings={noopVoid}
      />,
    );

    const playButton = screen.getByRole("button", {
      name: "テスト音を再生",
    });
    fireEvent.click(playButton);
    expect(screen.getByRole("button", { name: "再生中…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "再生中…" }));
    expect(audio.constructor).toHaveBeenCalledOnce();

    await act(async () => {
      resolveResume();
      await resume;
    });
    await waitFor(() => expect(audio.oscillator.start).toHaveBeenCalledOnce());
    act(() => {
      const onended = audio.oscillator.onended as
        | ((event: Event) => void)
        | null;
      onended?.(new Event("ended"));
    });

    expect(
      await screen.findByText("相手側の音量バーが動いたか確認してください。"),
    ).toHaveAttribute("role", "status");
    expect(
      screen.getByRole("button", { name: "テスト音を再生" }),
    ).toBeEnabled();
    expect(audio.context.close).toHaveBeenCalledOnce();
  });

  it("keeps meeting start available when test sound playback fails", async () => {
    Object.defineProperty(window, "AudioContext", {
      configurable: true,
      value: vi.fn(() => {
        throw new Error("audio unavailable");
      }),
    });
    render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({ sttBackend: "google" })}
        send={vi.fn<SendFn>()}
        onSettings={noopVoid}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "テスト音を再生" }));

    expect(
      await screen.findByText(
        "テスト音を再生できませんでした。端末の音量設定を確認してください。",
      ),
    ).toHaveAttribute("role", "status");
    expect(screen.getByRole("button", { name: "会議を開始" })).toBeEnabled();
  });

  it("stops the test sound and closes its context when leaving setup", async () => {
    const audio = installAudioContext();
    const view = render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({ sttBackend: "google" })}
        send={vi.fn<SendFn>()}
        onSettings={noopVoid}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "テスト音を再生" }));
    await waitFor(() => expect(audio.oscillator.start).toHaveBeenCalledOnce());
    view.unmount();

    expect(audio.oscillator.stop).toHaveBeenCalledTimes(2);
    expect(audio.context.close).toHaveBeenCalledOnce();
  });

  it("prepares then starts once from a single start action", () => {
    const send = vi.fn<SendFn>();
    const view = render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState()}
        send={send}
        onSettings={noopVoid}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "会議を開始" }));
    expect(send).toHaveBeenCalledExactlyOnceWith({ type: "init_stt" });
    expect(screen.getByRole("button", { name: "準備中…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "準備中…" }));
    view.rerender(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({ sttInitialized: true })}
        send={send}
        onSettings={noopVoid}
      />,
    );
    expect(send).toHaveBeenCalledTimes(2);
    expect(send).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: "start_meeting" }),
    );
    view.rerender(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({ sttInitialized: true })}
        send={send}
        onSettings={noopVoid}
      />,
    );
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("does not start after cancelling even if a late ready event arrives", () => {
    const send = vi.fn<SendFn>();
    const view = render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState()}
        send={send}
        onSettings={noopVoid}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "会議を開始" }));
    fireEvent.click(screen.getByRole("button", { name: "キャンセル" }));
    expect(send).toHaveBeenLastCalledWith({ type: "shutdown_stt" });
    view.rerender(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({ sttInitialized: true })}
        send={send}
        onSettings={noopVoid}
      />,
    );
    expect(send).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "取り消し中…" })).toBeDisabled();
    view.rerender(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({ sttStateRevision: 2 })}
        send={send}
        onSettings={noopVoid}
      />,
    );
    expect(screen.getByRole("button", { name: "会議を開始" })).toBeEnabled();
  });

  it.each(["error", "disconnect"])(
    "stops the pending start on %s and allows retry",
    (reason) => {
      const send = vi.fn<SendFn>();
      const view = render(
        <SetupScreen
          {...replyRouteProps}
          state={idleState()}
          send={send}
          onSettings={noopVoid}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "会議を開始" }));
      view.rerender(
        <SetupScreen
          {...replyRouteProps}
          state={idleState(
            reason === "error" ? { errorRevision: 1 } : { connected: false },
          )}
          send={send}
          onSettings={noopVoid}
        />,
      );
      expect(screen.getByText("開始できませんでした")).toBeVisible();
      view.rerender(
        <SetupScreen
          {...replyRouteProps}
          state={idleState({ errorRevision: 1, sttInitialized: true })}
          send={send}
          onSettings={noopVoid}
        />,
      );
      expect(send).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole("button", { name: "会議を開始" }));
      expect(send).toHaveBeenLastCalledWith(
        expect.objectContaining({ type: "start_meeting" }),
      );
    },
  );

  it("cancels preparation when leaving the screen", () => {
    const send = vi.fn<SendFn>();
    const view = render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState()}
        send={send}
        onSettings={noopVoid}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "会議を開始" }));
    view.unmount();
    expect(send).toHaveBeenLastCalledWith({ type: "shutdown_stt" });
  });

  it("offers model setup when the first download is missing", () => {
    const onSpeechSettings = vi.fn();
    render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState()}
        speechReadiness="missing"
        send={vi.fn()}
        onSettings={noopVoid}
        onSpeechSettings={onSpeechSettings}
      />,
    );
    expect(screen.getByRole("button", { name: "会議を開始" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "モデルを準備" }));
    expect(onSpeechSettings).toHaveBeenCalledOnce();
  });

  it("starts a prepared meeting with the choices the user supplied", () => {
    const send = vi.fn<SendFn>();
    render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({ sttBackend: "google" })}
        send={send}
        onSettings={noopVoid}
      />,
    );

    fireEvent.click(screen.getByText("会議の詳細・資料を追加"));
    fireEvent.change(screen.getByLabelText("会議の種類"), {
      target: { value: "商談" },
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "今日持ち帰りたいこと" }),
      { target: { value: "次回の担当者を決める" } },
    );
    fireEvent.change(screen.getByLabelText("あなたの立場"), {
      target: { value: "進行役" },
    });
    fireEvent.change(screen.getByLabelText("希望する話し方"), {
      target: { value: "率直に" },
    });
    fireEvent.click(screen.getByRole("button", { name: "会議を開始" }));

    expect(send).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "start_meeting",
        meeting_context: expect.objectContaining({
          scenario: "商談",
          userRole: "進行役",
          objective: "次回の担当者を決める",
          tone: "率直に",
        }),
        references: [],
      }),
    );
  });

  it("keeps meeting start available when reply setup is incomplete", () => {
    render(
      <SetupScreen
        {...replyRouteProps}
        replyStatus={{
          readiness: "setup_required",
          canGenerate: false,
          message: "返答案を利用する支援方法を設定してください。",
        }}
        state={idleState({ sttBackend: "google" })}
        send={vi.fn<SendFn>()}
        onSettings={noopVoid}
      />,
    );

    expect(
      screen.getByText(
        "返答案を利用できない場合も、録音と文字起こしは開始できます。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "AIを設定" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "会議を開始" })).toBeEnabled();
  });

  it("shows AI readiness loading without blocking meeting start", () => {
    render(
      <SetupScreen
        {...replyRouteProps}
        replyStatus={{
          readiness: "unknown",
          canGenerate: false,
          message: null,
        }}
        state={idleState({ sttBackend: "google" })}
        send={vi.fn<SendFn>()}
        onSettings={noopVoid}
      />,
    );

    expect(screen.getByText("AIの準備を確認しています…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "会議を開始" })).toBeEnabled();
  });

  it("shows the safe route error and retries without blocking meeting start", () => {
    const reload = vi.fn();
    render(
      <SetupScreen
        {...replyRouteProps}
        replyStatus={{
          readiness: "error",
          canGenerate: false,
          message:
            "支援方法の状態を確認できませんでした。しばらくしてから再度お試しください。",
        }}
        onReloadReplyStatus={reload}
        state={idleState({ sttBackend: "google" })}
        send={vi.fn<SendFn>()}
        onSettings={noopVoid}
      />,
    );

    expect(
      screen.getAllByText(
        "支援方法の状態を確認できませんでした。しばらくしてから再度お試しください。",
      ),
    ).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "再確認" }));
    expect(reload).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "会議を開始" })).toBeEnabled();
  });

  it.each([
    { readiness: "unknown" as const, message: null },
    {
      readiness: "error" as const,
      message: "支援方法の状態を確認できませんでした。",
    },
  ])(
    "prioritizes the disabled reply feature over route readiness $readiness",
    (status) => {
      render(
        <SetupScreen
          {...replyRouteProps}
          replyStatus={{ ...status, canGenerate: false }}
          state={idleState({
            sttBackend: "google",
            agentSettings: {
              replyEnabled: false,
              replyAutoGenerate: false,
              replyAgents: [],
            },
          })}
          send={vi.fn<SendFn>()}
          onSettings={noopVoid}
        />,
      );

      expect(screen.getAllByText("オフ・文字起こしのみ利用")).toHaveLength(1);
      expect(
        screen.queryByText("AIの準備を確認しています…"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "再確認" }),
      ).not.toBeInTheDocument();
      expect(
        screen.getAllByRole("button", { name: "設定" }).length,
      ).toBeGreaterThan(0);
    },
  );

  it("names unresolved devices as the resolved default speaker and microphone", () => {
    render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({
          devices: [
            {
              index: "speaker.monitor",
              name: "内蔵スピーカー",
              is_monitor: true,
              is_default: true,
            },
            {
              index: "microphone",
              name: "内蔵マイク",
              is_monitor: false,
              is_default: true,
            },
          ],
        })}
        send={vi.fn<SendFn>()}
        onSettings={noopVoid}
      />,
    );

    const speakerSelect = screen.getByRole<HTMLSelectElement>("combobox", {
      name: "相手側の音声",
    });
    const microphoneSelect = screen.getByRole<HTMLSelectElement>("combobox", {
      name: "自分のマイク",
    });

    expect(speakerSelect.selectedOptions[0]).toHaveTextContent(
      "既定スピーカー（内蔵スピーカー）",
    );
    expect(microphoneSelect.selectedOptions[0]).toHaveTextContent(
      "既定マイク（内蔵マイク）",
    );
    expect(screen.getAllByRole("group", { name: "スピーカー" })).toHaveLength(
      2,
    );
    expect(
      screen.queryByRole("group", { name: "この端末から聞こえる音" }),
    ).not.toBeInTheDocument();
  });

  it("blocks meeting start while the connection is unavailable", () => {
    render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({ connected: false, sttBackend: "google" })}
        send={vi.fn<SendFn>()}
        onSettings={noopVoid}
      />,
    );

    expect(screen.getByRole("button", { name: "会議を開始" })).toBeDisabled();
  });
});
