import { useEffect, useRef, useState, type ReactNode } from "react";
import { Dialog, DialogClose, DialogContent } from "../ui/Dialog";
import { Cloud, FolderOpen, HardDrive, Trash2 } from "lucide-react";
import {
  executeRecordingCleanup,
  previewRecordingCleanup,
  type RecordingCleanupPreview,
  type RecordingCleanupRequest,
} from "../../api/recordingRetention";
import type { AiRouteReadModel } from "../../hooks/useAiRoutes";
import { Button } from "../ui/Button";
import { FieldRow, SettingsSection, SettingsPage } from "./SettingsPrimitives";
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

function replyDestination(route: AiRouteReadModel | null): {
  text: string;
  local: boolean;
} {
  if (!route)
    return {
      text: "返答案に使うAIが未選択です。「返答案」で選ぶと送信先を確認できます。",
      local: true,
    };
  if (route.data_location === "local")
    return {
      text: `この端末内で処理します。${route.label}へ渡す会議のテキストは外部へ送りません。`,
      local: true,
    };
  return {
    text: `返答案を作るときに、必要な範囲の会議のテキストを${route.label}へ送ります。`,
    local: false,
  };
}

function DataFlowRow({
  label,
  local,
  children,
}: {
  label: string;
  local: boolean;
  children: ReactNode;
}) {
  const Icon = local ? HardDrive : Cloud;
  return (
    <div className="grid gap-1.5 md:grid-cols-[10rem_minmax(0,1fr)] md:gap-4">
      <p className="text-sm font-medium text-ink">{label}</p>
      <p className="flex items-start gap-2 text-sm leading-relaxed text-ink-muted">
        <Icon
          aria-hidden="true"
          className={`mt-0.5 size-4 shrink-0 ${local ? "text-positive" : "text-warning"}`}
        />
        <span>
          <span className="font-semibold text-ink">
            {local ? "この端末内" : "外部サービス"}
          </span>
          <span className="mx-1.5 text-ink-faint">—</span>
          {children}
        </span>
      </p>
    </div>
  );
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
  const destination = replyDestination(selectedRoute);
  const speechIsLocal =
    form.sttBackend === "whisper" || form.sttBackend === "reazonspeech";
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
      setCleanupPreview(null);
      setCleanupMessage(
        "削除結果を確認できませんでした。削除対象を再確認してください。",
      );
      setCleanupConfirmationOpen(false);
    } finally {
      setCleanupPending(false);
    }
  };

  return (
    <SettingsPage
      title="データと保存"
      description="会議のデータがどこで処理され、どこに保存されるかを確認できます。"
    >
      <SettingsSection
        title="データが処理される場所"
        description="現在の設定での扱いです。「返答案」で使うAIを変えると、テキストの送信先も変わります。"
      >
        <div className="space-y-3">
          {speechIsLocal && (
            <DataFlowRow label="会議の音声" local>
              文字起こしはこの端末で行い、音声を外部へ送りません。
            </DataFlowRow>
          )}
          <DataFlowRow label="会議のテキスト" local={destination.local}>
            {destination.text}
          </DataFlowRow>
        </div>
      </SettingsSection>

      <SettingsSection
        title="保存先"
        description="会議の履歴と録音は、この端末のフォルダに保存します。"
      >
        <div className="space-y-4">
          <FieldRow label="履歴と録音">
            <div className="break-all rounded-lg border border-line bg-paper px-3 py-2 text-xs leading-relaxed text-ink-muted">
              {form.dataDir || "保存先を確認しています"}
            </div>
          </FieldRow>
          <FieldRow
            label="共通の参考資料"
            hint="このフォルダの .md ファイルを、すべての会議で前提情報として使います"
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
              <Button
                variant="secondary"
                size="md"
                onClick={onChooseContextDirectory}
              >
                <FolderOpen className="size-4" aria-hidden="true" />
                選ぶ
              </Button>
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
              空欄にすると標準のフォルダへ戻ります。
            </p>
          </FieldRow>
        </div>
      </SettingsSection>

      <SettingsSection
        title="録音の整理"
        description="古い録音を、条件を決めてまとめて削除できます。自動では削除しません。条件を入力し、対象を確認してから削除します。"
      >
        <div className="space-y-4">
          <FieldRow
            label="終了日で選ぶ"
            hint="この日より前に終了した会議が対象です"
          >
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
            label="合計容量で選ぶ"
            hint="録音の合計がこの容量を超えた分を、古い会議から対象にします。空欄は無効です"
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
          <p className="text-xs leading-relaxed text-ink-muted">
            対象は終了済みの会議だけです。削除すると、録音と会議の履歴がまとめて完全に削除され、元に戻せません。
          </p>
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
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                void previewCleanup();
              }}
              disabled={cleanupPending}
            >
              削除対象を確認
            </Button>
            {cleanupPreview && cleanupPreview.delete_count > 0 && (
              <Button
                variant="danger"
                size="sm"
                onClick={() => setCleanupConfirmationOpen(true)}
                disabled={cleanupPending}
              >
                <Trash2 className="size-4" aria-hidden="true" />
                {cleanupPreview.delete_count}件を削除する
              </Button>
            )}
          </div>
        </div>
      </SettingsSection>
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
