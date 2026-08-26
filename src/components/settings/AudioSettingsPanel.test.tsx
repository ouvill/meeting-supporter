import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SpeechModelController } from "../../hooks/useSpeechModel";
import { AudioSettingsPanel } from "./AudioSettingsPanel";
import type { ConnectionProvider } from "./ApiConnectionControl";
import type { ConnectionUiState, SettingsForm } from "./types";

const FORM: SettingsForm = {
  secretsStatus: {},
  secretInputs: {},
  ollamaBaseUrl: "http://localhost:11434/v1",
  acpCommand: "",
  sttBackend: "whisper",
  sttWhisperModel: "large-v3-turbo",
  sttDeepgramModel: "nova-3",
  sttOpenaiModel: "gpt-4o-transcribe",
  sttVoskModelPath: "vosk-model-small-ja-0.22",
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
  it("offers Torch-free Silero VAD with a configurable threshold", () => {
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
    );

    const vadEngine = screen.getByLabelText("声の検出方法");
    expect(vadEngine).toHaveValue("silero");
    expect(
      screen.getByRole("option", { name: "Silero VAD（高精度・おすすめ）" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Silero音声判定しきい値")).toHaveValue("0.4");
    expect(
      screen.getByText(
        "SileroはTorchを使わず、同梱した約208 KBのONNXモデルを端末内で実行します",
      ),
    ).toBeInTheDocument();

    fireEvent.change(vadEngine, { target: { value: "webrtc" } });
    expect(update).toHaveBeenCalledWith("sttVadEngine", "webrtc");
  });
});
