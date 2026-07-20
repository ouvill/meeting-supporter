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
  it("guides a first meeting through audio before optional context", () => {
    const onSettings = vi.fn();
    render(
      <SetupScreen
        {...replyRouteProps}
        showFirstRunGuidance
        replyStatus={{
          readiness: "setup_required",
          canGenerate: false,
          message: "返答案を利用する支援方法を設定してください。",
        }}
        state={idleState({ sttBackend: "google" })}
        send={vi.fn<SendFn>()}
        onSettings={onSettings}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "まず、音声を確認しましょう" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "音が届くことを確かめれば、AIの設定はあとからでも大丈夫です。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "返答案は後から設定できます。今は録音と文字起こしだけでも会議を開始できます。",
      ),
    ).toBeInTheDocument();

    const headings = screen
      .getAllByRole("heading")
      .map((heading) => heading.textContent);
    expect(headings.indexOf("音声チェック")).toBeLessThan(
      headings.indexOf("会議について"),
    );

    fireEvent.click(screen.getByRole("button", { name: "AIも準備する" }));
    expect(onSettings).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "会議を開始" })).toBeEnabled();
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

  it("starts audio preparation from the setup flow", () => {
    const send = vi.fn<SendFn>();
    render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState()}
        send={send}
        onSettings={noopVoid}
      />,
    );

    expect(
      screen.getByText(
        "初回は必要なデータの読み込みに時間がかかる場合があります。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("音声認識の準備が必要です")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "音声認識を使えるようにする" }),
    );
    expect(send).toHaveBeenCalledWith({ type: "init_stt" });
  });

  it("lets a user cancel a pending audio preparation", () => {
    const send = vi.fn<SendFn>();
    render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({ sttInitRequested: true })}
        send={send}
        onSettings={noopVoid}
      />,
    );

    expect(screen.getByText("音声認識を準備しています…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "キャンセル" }));
    expect(send).toHaveBeenCalledWith({ type: "shutdown_stt" });
  });

  it("reports when speech recognition is ready", () => {
    const send = vi.fn<SendFn>();
    render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({ sttInitialized: true })}
        send={send}
        onSettings={noopVoid}
      />,
    );

    expect(screen.getByText("音声認識を使えます")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "やり直す" }));
    expect(send).toHaveBeenCalledWith({ type: "shutdown_stt" });
  });

  it("explains how to retry failed speech recognition preparation", () => {
    render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({ statusText: "エラー: initialization failed" })}
        send={vi.fn<SendFn>()}
        onSettings={noopVoid}
      />,
    );

    expect(
      screen.getByText(
        "音声認識を準備できませんでした。もう一度お試しください。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "音声認識を使えるようにする" }),
    ).toBeEnabled();
  });

  it("blocks meeting start until local audio preparation completes", () => {
    render(
      <SetupScreen
        {...replyRouteProps}
        state={idleState({ sttBackend: "vosk" })}
        send={vi.fn<SendFn>()}
        onSettings={noopVoid}
      />,
    );

    expect(screen.getByRole("button", { name: "会議を開始" })).toBeDisabled();
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

    expect(screen.getByText("開始できます")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "商談" }));
    fireEvent.change(
      screen.getByRole("textbox", { name: "今日持ち帰りたいこと" }),
      { target: { value: "次回の担当者を決める" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "進行役" }));
    fireEvent.click(screen.getByText("任意の詳細・資料"));
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
        "会話の記録は開始できます。返答案は現在利用できません。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "AIの準備を確認" }),
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
    ).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "もう一度試す" }));
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
              infoEnabled: true,
            },
          })}
          send={vi.fn<SendFn>()}
          onSettings={noopVoid}
        />,
      );

      expect(
        screen.getAllByText("返答案は設定でオフになっています。"),
      ).toHaveLength(2);
      expect(
        screen.queryByText("AIの準備を確認しています…"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "もう一度試す" }),
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
