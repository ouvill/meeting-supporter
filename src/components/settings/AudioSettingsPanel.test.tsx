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
  sttBackend: "reazonspeech",
  sttWhisperModel: "large-v3-turbo",
  sttDeepgramModel: "nova-3",
  sttOpenaiModel: "gpt-4o-transcribe",
  sttVoskModelPath: "vosk-model-small-ja-0.22",
  sttLang: "ja",
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
  backend: "reazonspeech",
  model: null,
  language: "ja",
  status: {
    backend: "reazonspeech",
    model_id: "reazonspeech-k2-v2-int8",
    state: "missing",
    phase: "idle",
    language: "ja",
    downloaded_bytes: 0,
    total_bytes: 160_372_200,
    progress_percent: null,
    model_path: null,
    storage_path: "/shared/huggingface-cache",
    error_code: null,
    message: "",
    retryable: true,
    cancelable: false,
  },
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
  it("offers ReazonSpeech as a local Japanese backend with managed preparation", () => {
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

    const backend = screen.getByLabelText("音声認識方式");
    expect(backend).toHaveValue("reazonspeech");
    expect(
      screen.getByRole("option", {
        name: "端末内・日本語高精度（ReazonSpeech）",
      }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("会議の言語")).toBeDisabled();
    expect(
      screen.getByText("ReazonSpeech K2-v2は日本語専用です"),
    ).toBeInTheDocument();
    expect(screen.getByText("ReazonSpeech日本語モデル")).toBeInTheDocument();

    fireEvent.change(backend, { target: { value: "whisper" } });
    expect(update).toHaveBeenCalledWith("sttBackend", "whisper");
  });
});
