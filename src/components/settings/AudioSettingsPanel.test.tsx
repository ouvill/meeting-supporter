import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getSpeechCapabilities } from "../../api/speechCapabilities";
import type { SpeechModelController } from "../../hooks/useSpeechModel";
import { AudioSettingsPanel } from "./AudioSettingsPanel";
import type { SettingsForm } from "./types";

vi.mock("../../api/speechCapabilities", () => ({
  getSpeechCapabilities: vi.fn(),
}));

const FORM: SettingsForm = {
  secretsStatus: {},
  secretInputs: {},
  ollamaBaseUrl: "http://localhost:11434/v1",
  sttBackend: "whisper",
  sttWhisperModel: "large-v3-turbo",
  sttDevice: "auto",
  sttLang: "ja",
  sttVadEngine: "silero",
  sttVadSensitivity: 0.4,
  sttSilence: 0.4,
  replyFeatureEnabled: true,
  replyAutoGenerate: false,
  replyStyles: [],
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

describe("AudioSettingsPanel", () => {
  beforeEach(() => {
    vi.mocked(getSpeechCapabilities).mockReset();
    vi.mocked(getSpeechCapabilities).mockResolvedValue({ whisper_gpu: true });
  });

  it("offers local speech controls", async () => {
    const update = vi.fn();
    render(
      <AudioSettingsPanel
        form={FORM}
        errors={{}}
        speechModel={SPEECH_MODEL}
        update={update}
      />,
    );
    expect(screen.getByRole("option", { name: "GPU" })).toBeDisabled();
    expect(
      screen.getByText("GPUへの対応状況を確認しています。"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("option", { name: "GPU" })).toBeEnabled(),
    );
    const device = screen.getByLabelText("音声認識の実行デバイス");
    fireEvent.change(device, {
      target: { value: "gpu" },
    });
    expect(update).toHaveBeenCalledWith("sttDevice", "gpu");
    expect(screen.getByRole("option", { name: "自動判定" })).toHaveValue(
      "auto",
    );
    expect(
      screen.queryByRole("option", { name: "CUDA" }),
    ).not.toBeInTheDocument();

    expect(
      screen.queryByRole("option", { name: "端末内・軽量" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Silero VAD")).toBeInTheDocument();
    expect(screen.queryByText(/WebRTC/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Silero音声判定しきい値")).toHaveValue("0.4");
    expect(
      screen.getByText(
        "SileroはTorchを使わず、同梱した約208 KBのONNXモデルを端末内で実行します",
      ),
    ).toBeInTheDocument();
  });

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

  it("locks every audio control while a meeting is active", async () => {
    render(
      <AudioSettingsPanel
        form={FORM}
        errors={{}}
        speechModel={SPEECH_MODEL}
        audioSettingsLocked
        update={vi.fn()}
      />,
    );

    expect(
      screen.getByText(
        "会議中は音声認識の設定を変更できません。会議を終了してから変更してください。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("音声認識方式")).toBeDisabled();
    expect(screen.getByLabelText("Silero音声判定しきい値")).toBeDisabled();
    await screen.findByText("自動はGPUを優先し、使えない場合はCPUで実行します");
  });

  it("offers ReazonSpeech as a Japanese-only local model", async () => {
    render(
      <AudioSettingsPanel
        form={{ ...FORM, sttBackend: "reazonspeech", sttLang: "ja" }}
        errors={{}}
        speechModel={{
          ...SPEECH_MODEL,
          backend: "reazonspeech",
          model: null,
        }}
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
    await waitFor(() => expect(getSpeechCapabilities).toHaveBeenCalled());
  });
});

function renderRustWhisper(device = "auto") {
  const update = vi.fn();
  render(
    <AudioSettingsPanel
      form={{ ...FORM, sttDevice: device }}
      errors={{}}
      speechModel={SPEECH_MODEL}
      update={update}
    />,
  );
  return update;
}
