import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SpeechModelStatusResponse } from "../../api/generated/types.gen";
import type { SpeechModelController } from "../../hooks/useSpeechModel";
import { SpeechModelPreparationCard } from "./SpeechModelPreparationCard";

function status(
  overrides: Partial<SpeechModelStatusResponse> = {},
): SpeechModelStatusResponse {
  return {
    backend: "vosk",
    model_id: "vosk-small-ja",
    state: "missing",
    phase: "idle",
    language: "ja",
    downloaded_bytes: 0,
    total_bytes: null,
    progress_percent: null,
    model_path: null,
    storage_path: "/app-data/speech",
    error_code: null,
    message: "",
    retryable: true,
    cancelable: false,
    ...overrides,
  };
}

function controller(
  overrides: Partial<SpeechModelController> = {},
): SpeechModelController {
  return {
    backend: "vosk",
    model: null,
    language: "ja",
    status: status(),
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
    ...overrides,
  };
}

describe("SpeechModelPreparationCard", () => {
  it("explains managed local preparation with capacity, network, and storage information without exposing technical model names", () => {
    render(
      <SpeechModelPreparationCard
        model={controller({
          status: status({
            storage_path: "/app-data/speech/japanese",
            model_path: "/app-data/vosk-model-small-ja-0.22",
          }),
        })}
      />,
    );

    expect(screen.getByText("軽量な音声認識データ")).toBeInTheDocument();
    expect(screen.getByText(/約48 MBのデータを使用します/)).toBeInTheDocument();
    expect(
      screen.getByText(
        /取得を始めたときだけインターネット通信を行います。会議の音声は送信しません。/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("保存先")).toBeInTheDocument();
    expect(screen.getByText("/app-data/speech/japanese")).toBeInTheDocument();
    expect(screen.queryByText(/vosk|model-small/i)).not.toBeInTheDocument();
  });

  it("reports determinate download progress as an accessible percentage and transferred capacity", () => {
    render(
      <SpeechModelPreparationCard
        model={controller({
          status: status({
            state: "downloading",
            phase: "downloading",
            downloaded_bytes: 25 * 1024 * 1024,
            total_bytes: 100 * 1024 * 1024,
            progress_percent: null,
            cancelable: true,
          }),
          isDownloading: true,
          blocksSettingsSave: true,
        })}
      />,
    );

    expect(
      screen.getByRole("progressbar", {
        name: "軽量な音声認識データの準備進捗",
      }),
    ).toHaveAttribute("aria-valuenow", "25");
    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuetext",
      "取得中 25%",
    );
    expect(screen.getByText("25.0 MB / 100.0 MB")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "取得を取り消す" }),
    ).toBeEnabled();
  });

  it("keeps unknown download progress indeterminate instead of inventing a percentage", () => {
    render(
      <SpeechModelPreparationCard
        model={controller({
          status: status({
            state: "downloading",
            phase: "verifying",
            downloaded_bytes: 12 * 1024 * 1024,
            total_bytes: null,
            progress_percent: null,
            cancelable: false,
          }),
          isDownloading: true,
          blocksSettingsSave: true,
        })}
      />,
    );

    const progress = screen.getByRole("progressbar", {
      name: "軽量な音声認識データの準備進捗",
    });
    expect(progress).not.toHaveAttribute("aria-valuenow");
    expect(progress).toHaveAttribute(
      "aria-valuetext",
      "データを確認中、進捗を確認中",
    );
    expect(screen.getByText("進捗を確認中")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "取得を取り消す" }),
    ).not.toBeInTheDocument();
  });

  it("offers an accessible retry with actionable network and storage recovery messages", () => {
    const { rerender } = render(
      <SpeechModelPreparationCard
        model={controller({
          status: status({ state: "failed", error_code: "network" }),
        })}
      />,
    );

    expect(
      screen.getByText(
        "通信が途切れました。接続を確認して、もう一度お試しください。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "もう一度取得" })).toBeEnabled();

    rerender(
      <SpeechModelPreparationCard
        model={controller({
          status: status({ state: "failed", error_code: "disk_full" }),
        })}
      />,
    );

    expect(
      screen.getByText(
        "保存先の空き容量が不足しています。不要なファイルを整理してから、もう一度お試しください。",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the unavailable-language start action disabled and presents ready and cancelled states without technical identifiers", () => {
    const { rerender } = render(
      <SpeechModelPreparationCard
        model={controller({
          language: null,
          status: status(),
          loading: false,
        })}
      />,
    );

    expect(screen.getByText("会議の言語を選んでください")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "データを取得" })).toBeDisabled();

    rerender(
      <SpeechModelPreparationCard
        model={controller({
          status: status({
            state: "ready",
            phase: "ready",
            model_path: "/private/vosk-model-small-ja-0.22",
          }),
        })}
      />,
    );
    expect(
      screen.getByText("軽量な音声認識データの準備ができました"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /取得/ }),
    ).not.toBeInTheDocument();

    rerender(
      <SpeechModelPreparationCard
        model={controller({
          status: status({ state: "cancelled", error_code: "cancelled" }),
        })}
      />,
    );
    expect(screen.getByText("取得を取り消しました")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "もう一度取得" })).toBeEnabled();
    expect(screen.queryByText(/vosk|model-small/i)).not.toBeInTheDocument();
  });

  it("identifies Whisper preparation by the selected quality and shared-cache location without a Vosk label", () => {
    const { rerender } = render(
      <SpeechModelPreparationCard
        model={controller({
          backend: "whisper",
          model: "small",
          status: status({
            backend: "whisper",
            model_id: "small",
            state: "downloading",
            phase: "downloading",
            total_bytes: 100 * 1024 * 1024,
            downloaded_bytes: 50 * 1024 * 1024,
            progress_percent: null,
            cancelable: false,
          }),
          isDownloading: true,
          blocksSettingsSave: true,
        })}
      />,
    );

    expect(screen.getByText("高精度な音声認識モデル")).toBeInTheDocument();
    expect(
      screen.getByText(
        /選択したバランスモデルを端末内で使えるように準備します/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Hugging Face の共有キャッシュ"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("progressbar", {
        name: "高精度な音声認識モデルの準備進捗",
      }),
    ).toHaveAttribute("aria-valuenow", "50");
    expect(
      screen.queryByRole("button", { name: "取得を取り消す" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Vosk/i)).not.toBeInTheDocument();

    rerender(
      <SpeechModelPreparationCard
        model={controller({
          backend: "whisper",
          model: "small",
          status: status({
            backend: "whisper",
            model_id: "small",
            state: "failed",
            error_code: "network",
          }),
        })}
      />,
    );
    expect(
      screen.getByText("高精度な音声認識モデルを準備できませんでした"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "もう一度取得" })).toBeEnabled();
  });

  it("presents ReazonSpeech as a fixed Japanese shared-cache model", () => {
    render(
      <SpeechModelPreparationCard
        model={controller({
          backend: "reazonspeech",
          model: null,
          language: "ja",
          status: status({
            backend: "reazonspeech",
            model_id: "reazonspeech-k2-v2-int8",
            total_bytes: 160_372_200,
          }),
        })}
      />,
    );

    expect(screen.getByText("ReazonSpeech日本語モデル")).toBeInTheDocument();
    expect(screen.getByText("日本語・約153 MB")).toBeInTheDocument();
    expect(
      screen.getByText("Hugging Face の共有キャッシュ"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "モデルを取得（約153 MB）" }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "取得を取り消す" }),
    ).not.toBeInTheDocument();
  });
});
