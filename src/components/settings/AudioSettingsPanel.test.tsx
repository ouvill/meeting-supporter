import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getSpeechCapabilities } from "../../api/speechCapabilities";
import type { SpeechModelController } from "../../hooks/useSpeechModel";
import { RuntimeContext } from "../../platform/runtimeContext";
import { AudioSettingsPanel } from "./AudioSettingsPanel";
import type { ConnectionProvider } from "./ApiConnectionControl";
import type { ConnectionUiState, SettingsForm } from "./types";

vi.mock("../../api/speechCapabilities", () => ({
  getSpeechCapabilities: vi.fn(),
}));

const FORM: SettingsForm = {
  secretsStatus: {},
  secretInputs: {},
  ollamaBaseUrl: "http://localhost:11434/v1",
  acpCommand: "",
  sttBackend: "whisper",
  sttWhisperModel: "large-v3-turbo",
  sttDevice: "auto",
  sttDeepgramModel: "nova-3",
  sttOpenaiModel: "gpt-4o-transcribe",
  sttLang: "ja",
  sttVadEngine: "silero",
  sttVadSensitivity: 0.4,
  sttVad: 2,
  sttSilence: 0.4,
  replyFeatureEnabled: true,
  replyAutoGenerate: false,
  replyStyles: [],
  infoFeatureEnabled: true,
  usageMeetingLimitJpy: 0,
  usageMonthlyLimitJpy: 0,
  dataDir: "",
  contextDir: "",
  recordingCleanupCutoffDate: "",
  recordingCleanupMaxMegabytes: 0,
};

const SPEECH_MODEL: SpeechModelController = {
  backend: "whisper",
  model: "large-v3-turbo",
  language: "ja",
  status: null,
  loading: false,
  action: null,
  error: null,
  confirmingStart: false,
  checkingStatus: false,
  isDownloading: false,
  blocksSettingsSave: false,
  refresh: vi.fn(async () => {}),
  startDownload: vi.fn(async () => {}),
  cancelDownload: vi.fn(async () => {}),
};

const CONNECTION_STATES: Record<ConnectionProvider, ConnectionUiState> = {
  openai: "unconfigured",
  deepgram: "unconfigured",
  xai: "unconfigured",
  gemini: "unconfigured",
  anthropic: "unconfigured",
};

describe("AudioSettingsPanel", () => {
  beforeEach(() => {
    vi.mocked(getSpeechCapabilities).mockReset();
    vi.mocked(getSpeechCapabilities).mockResolvedValue({ whisper_gpu: true });
  });

  it.each(["python", "rust-backend"] as const)(
    "offers local speech controls for %s",
    async (runtime) => {
      const update = vi.fn();
      render(
        <AudioSettingsPanel
          form={FORM}
          errors={{}}
          speechModel={SPEECH_MODEL}
          managedStt={{
            offered: false,
            loading: false,
            authenticated: false,
            selectable: false,
            message: "このビルドでは提供していません。",
            refresh: vi.fn(async () => {}),
          }}
          onManageAccount={vi.fn()}
          connectionStates={CONNECTION_STATES}
          secretsStatus={{}}
          secretInputs={{}}
          connectionEditingProvider={null}
          connectionTestingProvider={null}
          connectionTestMessages={{}}
          onBeginConnectionEdit={vi.fn()}
          onCancelConnectionEdit={vi.fn()}
          onSecretChange={vi.fn()}
          onTestConnection={vi.fn()}
          onRequestSecretDelete={vi.fn()}
          onCancelSecretDelete={vi.fn()}
          update={update}
        />,
        {
          wrapper: ({ children }) => (
            <RuntimeContext.Provider value={runtime}>
              {children}
            </RuntimeContext.Provider>
          ),
        },
      );
      if (runtime === "rust-backend") {
        expect(screen.getByRole("option", { name: "GPU" })).toBeDisabled();
        expect(
          screen.getByText("GPUへの対応状況を確認しています。"),
        ).toBeInTheDocument();
        await waitFor(() =>
          expect(screen.getByRole("option", { name: "GPU" })).toBeEnabled(),
        );
      } else {
        expect(getSpeechCapabilities).not.toHaveBeenCalled();
        expect(screen.getByRole("option", { name: "CUDA" })).toBeEnabled();
      }
      const device = screen.getByLabelText("音声認識の実行デバイス");
      fireEvent.change(device, {
        target: { value: runtime === "rust-backend" ? "gpu" : "cuda" },
      });
      expect(update).toHaveBeenCalledWith(
        "sttDevice",
        runtime === "rust-backend" ? "gpu" : "cuda",
      );
      if (runtime === "rust-backend") {
        expect(screen.getByRole("option", { name: "自動判定" })).toHaveValue(
          "auto",
        );
        expect(
          screen.queryByRole("option", { name: "CUDA" }),
        ).not.toBeInTheDocument();
      }

      expect(
        screen.queryByRole("option", { name: "端末内・軽量" }),
      ).not.toBeInTheDocument();
      const vadEngine = screen.getByLabelText("声の検出方法");
      expect(vadEngine).toHaveValue("silero");
      expect(
        screen.getByRole("option", { name: "Silero VAD（高精度・おすすめ）" }),
      ).toBeInTheDocument();
      expect(screen.getByLabelText("Silero音声判定しきい値")).toHaveValue(
        "0.4",
      );
      expect(
        screen.getByText(
          "SileroはTorchを使わず、同梱した約208 KBのONNXモデルを端末内で実行します",
        ),
      ).toBeInTheDocument();

      fireEvent.change(vadEngine, { target: { value: "webrtc" } });
      expect(update).toHaveBeenCalledWith("sttVadEngine", "webrtc");
    },
  );

  it.each(["auto", "gpu"])(
    "disables GPU in a CPU-only build without changing saved %s",
    async (device) => {
      vi.mocked(getSpeechCapabilities).mockResolvedValue({
        whisper_gpu: false,
      });
      const update = renderRustWhisper(device);
      const message = await screen.findByText(
        "このビルドはGPUに対応していません。「自動」または「CPU」を選んでください。",
      );
      const select = screen.getByLabelText("音声認識の実行デバイス");
      expect(select).toHaveValue(device);
      expect(select).toHaveAccessibleDescription(message.textContent!);
      expect(screen.getByRole("option", { name: "GPU" })).toBeDisabled();
      expect(update).not.toHaveBeenCalled();
      fireEvent.change(select, { target: { value: "gpu" } });
      expect(update).not.toHaveBeenCalled();
      for (const value of ["cpu", "auto"]) {
        fireEvent.change(select, { target: { value } });
        expect(update).toHaveBeenLastCalledWith("sttDevice", value);
      }
    },
  );

  it.each(["unknown", "error"])(
    "keeps GPU disabled when worker support is %s",
    async (state) => {
      if (state === "error") {
        vi.mocked(getSpeechCapabilities).mockRejectedValue(
          new Error("unavailable"),
        );
      } else {
        vi.mocked(getSpeechCapabilities).mockResolvedValue({
          whisper_gpu: null,
        });
      }
      renderRustWhisper();
      await screen.findByText(/GPUへの対応状況を確認できませんでした/);
      expect(screen.getByRole("option", { name: "GPU" })).toBeDisabled();
      expect(screen.getByRole("option", { name: "CPU" })).toBeEnabled();
      expect(screen.getByRole("option", { name: "自動" })).toBeEnabled();
      expect(
        screen.queryByText(/このビルドはGPUに対応していません/),
      ).not.toBeInTheDocument();
    },
  );

  it("locks every audio control while a meeting is active", () => {
    render(
      <AudioSettingsPanel
        form={FORM}
        errors={{}}
        speechModel={SPEECH_MODEL}
        audioSettingsLocked
        managedStt={{
          offered: false,
          loading: false,
          authenticated: false,
          selectable: false,
          message: "このビルドでは提供していません。",
          refresh: vi.fn(async () => {}),
        }}
        onManageAccount={vi.fn()}
        connectionStates={CONNECTION_STATES}
        secretsStatus={{}}
        secretInputs={{}}
        connectionEditingProvider={null}
        connectionTestingProvider={null}
        connectionTestMessages={{}}
        onBeginConnectionEdit={vi.fn()}
        onCancelConnectionEdit={vi.fn()}
        onSecretChange={vi.fn()}
        onTestConnection={vi.fn()}
        onRequestSecretDelete={vi.fn()}
        onCancelSecretDelete={vi.fn()}
        update={vi.fn()}
      />,
    );

    expect(
      screen.getByText(
        "会議中は音声認識の設定を変更できません。会議を終了してから変更してください。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("音声認識方式")).toBeDisabled();
    expect(screen.getByLabelText("声の検出方法")).toBeDisabled();
    expect(screen.getByLabelText("Silero音声判定しきい値")).toBeDisabled();
  });

  it("offers ReazonSpeech as a Japanese-only local model", () => {
    render(
      <AudioSettingsPanel
        form={{ ...FORM, sttBackend: "reazonspeech", sttLang: "ja" }}
        errors={{}}
        speechModel={{
          ...SPEECH_MODEL,
          backend: "reazonspeech",
          model: null,
        }}
        managedStt={{
          offered: false,
          loading: false,
          authenticated: false,
          selectable: false,
          message: "このビルドでは提供していません。",
          refresh: vi.fn(async () => {}),
        }}
        onManageAccount={vi.fn()}
        connectionStates={CONNECTION_STATES}
        secretsStatus={{}}
        secretInputs={{}}
        connectionEditingProvider={null}
        connectionTestingProvider={null}
        connectionTestMessages={{}}
        onBeginConnectionEdit={vi.fn()}
        onCancelConnectionEdit={vi.fn()}
        onSecretChange={vi.fn()}
        onTestConnection={vi.fn()}
        onRequestSecretDelete={vi.fn()}
        onCancelSecretDelete={vi.fn()}
        update={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("option", {
        name: "端末内・日本語高精度（ReazonSpeech）",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "ReazonSpeech K2-v2の軽量化モデルを端末内で実行します。日本語専用で、モデルの取得に約153 MB使用します。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("会議の言語")).toBeDisabled();
    expect(
      screen.queryByRole("option", { name: "英語" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("ReazonSpeech日本語モデル")).toBeInTheDocument();
  });
});

function renderRustWhisper(device = "auto") {
  const update = vi.fn();
  render(
    <RuntimeContext.Provider value="rust-backend">
      <AudioSettingsPanel
        form={{ ...FORM, sttDevice: device }}
        errors={{}}
        speechModel={SPEECH_MODEL}
        managedStt={{
          offered: false,
          loading: false,
          authenticated: false,
          selectable: false,
          message: "このビルドでは提供していません。",
          refresh: vi.fn(async () => {}),
        }}
        onManageAccount={vi.fn()}
        connectionStates={CONNECTION_STATES}
        secretsStatus={{}}
        secretInputs={{}}
        connectionEditingProvider={null}
        connectionTestingProvider={null}
        connectionTestMessages={{}}
        onBeginConnectionEdit={vi.fn()}
        onCancelConnectionEdit={vi.fn()}
        onSecretChange={vi.fn()}
        onTestConnection={vi.fn()}
        onRequestSecretDelete={vi.fn()}
        onCancelSecretDelete={vi.fn()}
        update={update}
      />
    </RuntimeContext.Provider>,
  );
  return update;
}
