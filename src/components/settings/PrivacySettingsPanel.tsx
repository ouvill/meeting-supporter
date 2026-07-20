import { useEffect, useRef, useState } from "react";
import { Dialog, DialogClose, DialogContent } from "../ui/Dialog";
import { Cloud, FolderOpen, HardDrive, Trash2 } from "lucide-react";
import {
  executeRecordingCleanup,
  previewRecordingCleanup,
  type RecordingCleanupPreview,
  type RecordingCleanupRequest,
} from "../../api/recordingRetention";
import type { AiRouteReadModel } from "../../hooks/useAiRoutes";
import { FieldRow, SettingsCard, SettingsPage } from "./SettingsPrimitives";
import type { SettingsFieldErrors, SettingsForm } from "./types";

interface Props {
  form: SettingsForm;
  selectedRoute: AiRouteReadModel | null;
  errors: SettingsFieldErrors;
  update: <K extends keyof SettingsForm>(
    key: K,
    value: SettingsForm[K],
  ) => void;
  onChooseContextDirectory: () => void;
}

function destinationCopy(route: AiRouteReadModel | null): {
  title: string;
  description: string;
  local: boolean;
} {
  if (!route) {
    return {
      title: "支援方法が未選択です",
      description:
        "「支援方法」で利用する方法を選ぶと、会議テキストの送信先を確認できます。",
      local: true,
    };
  }
  if (route.data_location === "local") {
    return {
      title: "この端末内で処理します",
      description:
        "返答支援のための会議テキストは、選択中の方法では外部へ送信されません。",
      local: true,
    };
  }
  return {
    title: "会議テキストを外部へ送ります",
    description:
      "返答支援に必要な範囲の会議テキストが、選択したサービスへ送信されます。音声の送信範囲は「音声」の処理方法で決まります。",
    local: false,
  };
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${bytes.toLocaleString()} B`;
  return `${(bytes / (1024 * 1024)).toLocaleString(undefined, { maximumFractionDigits: 1 })} MB`;
}

function CleanupConfirmationDialog({
  open,
  pending,
  preview,
  conditions,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  pending: boolean;
  preview: RecordingCleanupPreview | null;
  conditions: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  if (!preview) return null;

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => !nextOpen && !pending && onCancel()}
    >
      <DialogContent
        title="録音を削除しますか？"
        description={`${conditions}。終了済みの会議 ${preview.delete_count}件と録音 ${formatBytes(preview.delete_recording_bytes)} を完全に削除します。`}
        showClose={false}
        className="max-w-md"
      >
        <div className="p-6">
          <p className="text-xs font-semibold text-danger">
            この操作は取り消せません。
          </p>
          <div className="mt-6 flex flex-wrap justify-end gap-2">
            <DialogClose
              ref={cancelButtonRef}
              type="button"
              disabled={pending}
              autoFocus
              className="rounded-lg border border-line bg-surface px-3 py-2 text-xs font-semibold text-ink-muted hover:border-line-strong disabled:cursor-not-allowed disabled:opacity-50"
            >
              キャンセル
            </DialogClose>
            <button
              type="button"
              onClick={onConfirm}
              disabled={pending}
              className="rounded-lg border border-danger bg-danger px-3 py-2 text-xs font-semibold text-white hover:bg-danger/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              削除を実行する
            </button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function PrivacySettingsPanel({
  form,
  selectedRoute,
  errors,
  update,
  onChooseContextDirectory,
}: Props) {
  const destination = destinationCopy(selectedRoute);
  const [cleanupPreview, setCleanupPreview] =
    useState<RecordingCleanupPreview | null>(null);
  const [cleanupMessage, setCleanupMessage] = useState<string | null>(null);
  const [cleanupPending, setCleanupPending] = useState(false);
  const [cleanupConfirmationOpen, setCleanupConfirmationOpen] = useState(false);
  const cleanupRequest: RecordingCleanupRequest = {
    cutoff_date: form.recordingCleanupCutoffDate || null,
    max_total_bytes:
      form.recordingCleanupMaxMegabytes > 0
        ? Math.floor(form.recordingCleanupMaxMegabytes * 1024 * 1024)
        : null,
  };
  const cleanupConditions = [
    form.recordingCleanupCutoffDate
      ? `${form.recordingCleanupCutoffDate} より前に終了`
      : null,
    form.recordingCleanupMaxMegabytes > 0
      ? `録音合計を ${form.recordingCleanupMaxMegabytes} MB 以下`
      : null,
  ]
    .filter(Boolean)
    .join("、");

  useEffect(() => {
    setCleanupPreview(null);
    setCleanupMessage(null);
    setCleanupConfirmationOpen(false);
  }, [form.recordingCleanupCutoffDate, form.recordingCleanupMaxMegabytes]);

  const previewCleanup = async () => {
    if (!cleanupRequest.cutoff_date && !cleanupRequest.max_total_bytes) {
      setCleanupMessage("削除条件として日付または最大容量を入力してください。");
      return;
    }
    setCleanupPending(true);
    setCleanupMessage(null);
    try {
      const preview = await previewRecordingCleanup(cleanupRequest);
      setCleanupPreview(preview);
      setCleanupConfirmationOpen(false);
    } catch {
      setCleanupMessage(
        "削除対象を確認できませんでした。しばらくしてから再試行してください。",
      );
    } finally {
      setCleanupPending(false);
    }
  };

  const executeCleanup = async () => {
    if (!cleanupPreview) return;
    setCleanupPending(true);
    setCleanupMessage(null);
    try {
      const result = await executeRecordingCleanup(cleanupRequest);
      setCleanupPreview(null);
      setCleanupConfirmationOpen(false);
      setCleanupMessage(
        result.failed_meeting_ids.length
          ? `${result.deleted_meeting_ids.length}件を削除しました。${result.failed_meeting_ids.length}件は削除できませんでした。`
          : `${result.deleted_meeting_ids.length}件を削除しました。`,
      );
    } catch {
      setCleanupMessage(
        "削除できませんでした。会議履歴は保持されています。再試行してください。",
      );
      setCleanupConfirmationOpen(false);
    } finally {
      setCleanupPending(false);
    }
  };

  return (
    <SettingsPage
      title="データとプライバシー"
      description="会議データを保存する場所と、支援を利用するときの送信範囲を確認できます。"
    >
      <SettingsCard title="返答支援で送られるデータ">
        <div
          className={`flex items-start gap-3 rounded-xl p-3.5 ${destination.local ? "bg-positive-soft text-positive" : "bg-warning-soft text-warning"}`}
        >
          {destination.local ? (
            <HardDrive className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
          ) : (
            <Cloud className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
          )}
          <div>
            <p className="text-xs font-bold">{destination.title}</p>
            <p className="mt-1 text-xs leading-relaxed opacity-80">
              {destination.description}
            </p>
          </div>
        </div>
      </SettingsCard>

      <SettingsCard
        title="端末内の保存先"
        description="会議履歴や録音は、このアプリのデータフォルダに保存されます。"
      >
        <div className="space-y-4">
          <FieldRow label="アプリのデータ">
            <div className="break-all rounded-lg border border-line bg-paper px-3 py-2 text-xs leading-relaxed text-ink-muted">
              {form.dataDir || "保存先を確認しています"}
            </div>
          </FieldRow>
          <FieldRow
            label="会議の前提資料"
            hint="このフォルダ内の .md ファイルを会議の前提情報として利用します"
            error={errors.contextDir}
          >
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={form.contextDir}
                onChange={(event) => update("contextDir", event.target.value)}
                placeholder={
                  form.dataDir ? `${form.dataDir}/context` : "標準のフォルダ"
                }
                className="field min-w-0 flex-1"
                aria-label="会議の前提資料フォルダ"
                aria-invalid={Boolean(errors.contextDir)}
              />
              <button
                type="button"
                onClick={onChooseContextDirectory}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-semibold text-ink hover:border-primary/45 hover:text-primary"
              >
                <FolderOpen className="h-3.5 w-3.5" aria-hidden="true" />
                選ぶ
              </button>
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
              空欄にすると標準のフォルダへ戻ります。資料そのものは自動で外部へ公開されません。
            </p>
          </FieldRow>
        </div>
      </SettingsCard>

      <SettingsCard
        title="録音の整理"
        description="自動では削除されません。条件を保存しても、下の確認と削除を実行するまで録音と会議履歴は保持されます。"
      >
        <div className="space-y-4">
          <FieldRow label="この日より前に終了した会議">
            <input
              type="date"
              value={form.recordingCleanupCutoffDate}
              onChange={(event) =>
                update("recordingCleanupCutoffDate", event.target.value)
              }
              className="field w-full"
              aria-label="録音を削除する終了日"
            />
          </FieldRow>
          <FieldRow
            label="録音の最大合計容量"
            hint="超えた分は、終了済み会議を古い順に削除します。0 は無効です。"
          >
            <div className="flex items-center gap-2">
              <input
                type="number"
                min="0"
                step="1"
                value={form.recordingCleanupMaxMegabytes || ""}
                onChange={(event) => {
                  const megabytes = Number(event.target.value);
                  update(
                    "recordingCleanupMaxMegabytes",
                    Number.isFinite(megabytes) && megabytes > 0 ? megabytes : 0,
                  );
                }}
                className="field min-w-0 flex-1"
                aria-label="録音の最大合計容量（MB）"
              />
              <span className="text-xs font-medium text-ink-faint">MB</span>
            </div>
          </FieldRow>
          <div className="rounded-xl border border-warning/20 bg-warning-soft p-3 text-xs text-warning">
            <p className="font-semibold">終了済みの会議だけが対象です</p>
            <p className="mt-1 leading-relaxed">
              進行中の会議は削除しません。削除すると録音ファイルと会議履歴が一緒に完全に削除されます。
            </p>
          </div>
          {cleanupPreview && (
            <div
              className="rounded-xl border border-line bg-surface-muted p-3 text-xs text-ink-muted"
              role="status"
            >
              {cleanupPreview.delete_count > 0 ? (
                <>
                  <p className="font-semibold">
                    {cleanupPreview.delete_count}件、
                    {formatBytes(cleanupPreview.delete_recording_bytes)}{" "}
                    を削除します。
                  </p>
                  <p className="mt-1">
                    削除後の録音容量:{" "}
                    {formatBytes(cleanupPreview.total_recording_bytes_after)}
                  </p>
                </>
              ) : (
                <p className="font-semibold">削除対象はありません。</p>
              )}
            </div>
          )}
          {cleanupMessage && (
            <p className="text-xs leading-relaxed text-ink-muted" role="status">
              {cleanupMessage}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                void previewCleanup();
              }}
              disabled={cleanupPending}
              className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-xs font-semibold text-ink-muted hover:border-primary/45 hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              削除対象を確認
            </button>
            {cleanupPreview && cleanupPreview.delete_count > 0 && (
              <button
                type="button"
                onClick={() => setCleanupConfirmationOpen(true)}
                disabled={cleanupPending}
                className="rounded-lg border border-danger bg-danger px-3 py-2 text-xs font-semibold text-white hover:bg-danger/90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {cleanupPreview.delete_count}件を削除する
              </button>
            )}
          </div>
        </div>
      </SettingsCard>
      <CleanupConfirmationDialog
        open={cleanupConfirmationOpen}
        pending={cleanupPending}
        preview={cleanupPreview}
        conditions={cleanupConditions}
        onCancel={() => setCleanupConfirmationOpen(false)}
        onConfirm={() => {
          void executeCleanup();
        }}
      />
    </SettingsPage>
  );
}
