import { Download, RotateCcw, X } from "lucide-react";
import type {
  SpeechModelController,
  WhisperModelAlias,
} from "../../hooks/useSpeechModel";
import type { SpeechModelStatusResponse } from "../../api/generated/types.gen";
import { Button, InlineNotice, Status, type StatusTone } from "../ui";

interface Props {
  model: SpeechModelController;
  startDisabled?: boolean;
}

const WHISPER_MODEL_LABEL: Record<WhisperModelAlias, string> = {
  tiny: "最速",
  base: "軽量",
  small: "バランス",
  medium: "高精度",
  "large-v2": "より高精度",
  "large-v3-turbo": "高速・高精度",
};

const FAILURE_RECOVERY: Record<
  NonNullable<SpeechModelStatusResponse["error_code"]>,
  string
> = {
  network: "通信が途切れました。接続を確認して、もう一度お試しください。",
  disk_full:
    "保存先の空き容量が不足しています。不要なファイルを整理してから、もう一度お試しください。",
  permission:
    "保存先に書き込めません。アプリの保存先へのアクセスを確認して、もう一度お試しください。",
  checksum:
    "取得したデータを安全に確認できませんでした。通信状態を確認して、もう一度お試しください。",
  archive: "取得したデータを準備できませんでした。もう一度取得してください。",
  cancelled: "取得を取り消しました。必要になったら、もう一度取得できます。",
  unknown:
    "準備中に問題が起きました。しばらくしてから、もう一度お試しください。",
};

function statePresentation(
  status: SpeechModelStatusResponse | null,
  loading: boolean,
  confirmingStart: boolean,
  action: SpeechModelController["action"],
): {
  tone: StatusTone;
  label: string;
} {
  if (action === "starting") return { tone: "busy", label: "取得を開始中" };
  if (action === "cancelling")
    return { tone: "busy", label: "取得を取り消しています" };
  if (confirmingStart) return { tone: "busy", label: "開始状況を確認中" };
  if (loading && status === null)
    return { tone: "busy", label: "準備状況を確認中" };
  if (status === null)
    return { tone: "neutral", label: "状況を確認できません" };
  if (status.state === "missing") return { tone: "neutral", label: "未取得" };
  if (status.state === "ready") return { tone: "positive", label: "準備済み" };
  if (status.state === "failed")
    return { tone: "danger", label: "取得できませんでした" };
  if (status.state === "cancelled")
    return { tone: "warning", label: "取り消しました" };
  if (status.phase === "verifying")
    return { tone: "busy", label: "データを確認中" };
  if (status.phase === "extracting")
    return { tone: "busy", label: "使用準備中" };
  return { tone: "busy", label: "取得中" };
}

function progressPercent(status: SpeechModelStatusResponse): number | null {
  if (status.progress_percent != null)
    return Math.min(100, Math.max(0, status.progress_percent));
  if (status.total_bytes && status.total_bytes > 0)
    return Math.min(
      100,
      Math.max(0, (status.downloaded_bytes / status.total_bytes) * 100),
    );
  return null;
}

function formatMegabytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function SpeechModelPreparationCard({
  model,
  startDisabled = false,
}: Props) {
  const { language, status } = model;
  const isWhisper = model.backend === "whisper";
  const isReazonSpeech = model.backend === "reazonspeech";
  const whisperModelLabel = model.model
    ? WHISPER_MODEL_LABEL[model.model]
    : "精度";
  const presentation = statePresentation(
    status,
    model.loading,
    model.confirmingStart,
    model.action,
  );
  const size = isReazonSpeech ? 153 : null;
  const percent =
    status?.state === "downloading" ? progressPercent(status) : null;
  const roundedPercent = percent === null ? null : Math.round(percent);
  const totalLabel =
    status?.state === "downloading" && status.total_bytes
      ? formatMegabytes(status.total_bytes)
      : size
        ? `約${size} MB`
        : "合計を確認中";
  const preparationName = isWhisper
    ? "高精度な音声認識モデル"
    : "ReazonSpeech日本語モデル";

  return (
    <section aria-label="音声認識モデルの準備">
      <div className="space-y-4">
        <div
          className="flex flex-wrap items-center justify-between gap-2"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          <Status tone={presentation.tone}>{presentation.label}</Status>
          {isWhisper ? (
            <span className="text-xs font-semibold text-ink-muted">
              {whisperModelLabel}
            </span>
          ) : isReazonSpeech ? (
            <span className="text-xs font-semibold tabular-nums text-ink-muted">
              日本語・約153 MB
            </span>
          ) : null}
        </div>

        {status?.state !== "ready" && (
          <div className="space-y-2 text-xs leading-relaxed text-ink-muted">
            <p>
              取得を始めたときだけインターネット通信を行います。会議の音声は送信しません。
            </p>
          </div>
        )}

        {language === null && (
          <InlineNotice tone="warning" title="会議の言語を選んでください">
            {isReazonSpeech
              ? "ReazonSpeechは日本語の会議で利用できます。"
              : "会議の言語を選ぶと、選択した精度モデルを準備できます。"}
          </InlineNotice>
        )}

        {model.error && (
          <InlineNotice tone="danger" title="操作を完了できませんでした">
            <div className="space-y-2">
              <p>{model.error}</p>
              {status === null && (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    void model.refresh();
                  }}
                  loading={model.loading}
                >
                  <RotateCcw aria-hidden="true" className="size-3.5" />
                  もう一度確認
                </Button>
              )}
            </div>
          </InlineNotice>
        )}

        {(model.action === "starting" ||
          model.confirmingStart ||
          model.isDownloading) && (
          <p className="text-xs text-ink-muted">
            設定を閉じても取得は続きます。
          </p>
        )}

        {status?.state === "downloading" && (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <div className="flex items-baseline justify-between gap-3 text-xs">
                <span className="font-semibold text-ink">
                  {presentation.label}
                </span>
                <span className="font-bold tabular-nums text-primary">
                  {roundedPercent === null
                    ? "進捗を確認中"
                    : `${roundedPercent}%`}
                </span>
              </div>
              <div
                className="h-2 overflow-hidden rounded-full bg-surface-muted"
                role="progressbar"
                aria-label={`${preparationName}の準備進捗`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={roundedPercent ?? undefined}
                aria-valuetext={
                  roundedPercent === null
                    ? `${presentation.label}、進捗を確認中`
                    : `${presentation.label} ${roundedPercent}%`
                }
              >
                {percent === null ? (
                  <div className="h-full w-1/3 animate-pulse rounded-full bg-primary/60 motion-reduce:animate-none" />
                ) : (
                  <div
                    className="h-full rounded-full bg-primary transition-[width] motion-reduce:transition-none"
                    style={{ width: `${percent}%` }}
                  />
                )}
              </div>
              <p className="text-right text-xs tabular-nums text-ink-muted">
                {formatMegabytes(status.downloaded_bytes)} / {totalLabel}
              </p>
            </div>
            {status.cancelable && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  void model.cancelDownload();
                }}
                loading={model.action === "cancelling"}
              >
                <X aria-hidden="true" className="size-3.5" />
                取得を取り消す
              </Button>
            )}
          </div>
        )}

        {status?.state === "missing" && (
          <div className="space-y-3">
            <p className="text-xs leading-relaxed text-ink-muted">
              {isReazonSpeech
                ? "取得後はReazonSpeechを端末内で使用できます。"
                : "取得後は選択した精度モデルを端末内で使用できます。"}
            </p>
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                void model.startDownload();
              }}
              loading={model.action === "starting"}
              disabled={
                language === null || model.confirmingStart || startDisabled
              }
            >
              <Download aria-hidden="true" className="size-3.5" />
              {`モデルをダウンロード${isReazonSpeech ? "（約153 MB）" : ""}`}
            </Button>
          </div>
        )}

        {status?.state === "failed" && (
          <InlineNotice
            tone="danger"
            title={`${preparationName}を準備できませんでした`}
          >
            <div className="space-y-2">
              <p>
                {status.error_code
                  ? FAILURE_RECOVERY[status.error_code]
                  : FAILURE_RECOVERY.unknown}
              </p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  void model.startDownload();
                }}
                loading={model.action === "starting"}
                disabled={model.confirmingStart || startDisabled}
              >
                <RotateCcw aria-hidden="true" className="size-3.5" />
                もう一度取得
              </Button>
            </div>
          </InlineNotice>
        )}

        {status?.state === "cancelled" && (
          <InlineNotice tone="warning" title="取得を取り消しました">
            <div className="space-y-2">
              <p>
                途中のデータは使用されません。必要になったら、いつでも再開できます。
              </p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  void model.startDownload();
                }}
                loading={model.action === "starting"}
                disabled={model.confirmingStart || startDisabled}
              >
                <Download aria-hidden="true" className="size-3.5" />
                もう一度取得
              </Button>
            </div>
          </InlineNotice>
        )}
      </div>
    </section>
  );
}
