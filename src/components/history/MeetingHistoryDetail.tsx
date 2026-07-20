import { useCallback, useEffect, useRef, useState } from "react";
import { Dialog, DialogClose, DialogContent } from "../ui/Dialog";
import { Tooltip } from "../ui/Tooltip";
import {
  AlertCircle,
  CalendarDays,
  Check,
  Clock3,
  MessageSquareReply,
  Mic2,
  Pencil,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import type {
  MeetingDetail,
  ReplySuggestionItem,
  TurnItem,
} from "../../api/generated/types.gen";
import type { AiUseCaseRouteStatus } from "../../hooks/useAiRoutes";
import { RecordingPlayer } from "./RecordingPlayer";

interface Props {
  meeting: MeetingDetail;
  loadingDetail: boolean;
  saving: boolean;
  deleting: boolean;
  error?: string | null;
  onRetry?: () => void;
  onUpdateTitle: (id: string, title: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  minutesStatus?: "idle" | "generating" | "cancelled" | "error";
  minutesProgress?: string;
  minutesError?: string | null;
  minutesRouteStatus?: AiUseCaseRouteStatus;
  onGenerateMinutes?: (id: string) => Promise<void>;
  onCancelMinutes?: () => void;
  onSettings?: () => void;
}

// ── Helpers ──────────────────────────────────────────────────────

function formatDate(iso: string): string {
  const d = new Date(iso);
  const y = d.getFullYear();
  const mo = d.getMonth() + 1;
  const da = d.getDate();
  const h = d.getHours().toString().padStart(2, "0");
  const mi = d.getMinutes().toString().padStart(2, "0");
  return `${y}年${mo}月${da}日 ${h}:${mi}`;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "--";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m < 1) return `${s}秒`;
  return `${m}分${s}秒`;
}

function statusLabel(status: string): string {
  switch (status) {
    case "completed":
      return "完了";
    case "aborted":
      return "中断";
    default:
      return "記録済み";
  }
}

function statusColor(status: string): string {
  switch (status) {
    case "completed":
      return "bg-positive-soft text-positive";
    case "aborted":
      return "bg-warning-soft text-warning";
    default:
      return "bg-surface-muted text-ink-muted";
  }
}

function formatRelativeTime(
  createdAt: string | null | undefined,
  startedAt: string,
): string | null {
  if (!createdAt) return null;
  const created = new Date(createdAt).getTime();
  const started = new Date(startedAt).getTime();
  if (!Number.isFinite(created) || !Number.isFinite(started)) return null;
  const totalSeconds = Math.max(0, Math.floor((created - started) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? `${hours}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`
    : `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

// ── Inline Title Editor ──────────────────────────────────────────

function InlineTitleEditor({
  title,
  meetingId,
  onSave,
  saving,
}: {
  title: string | null | undefined;
  meetingId: string;
  onSave: (id: string, title: string) => Promise<void>;
  saving: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title ?? "");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const wasEditingRef = useRef(false);
  const displayTitle = title || "タイトル未設定";

  useEffect(() => {
    if (wasEditingRef.current && !editing) headingRef.current?.focus();
    wasEditingRef.current = editing;
  }, [editing]);

  const commitSave = useCallback(
    (value: string) => {
      const nextTitle = value.trim();
      if (nextTitle && nextTitle !== title) void onSave(meetingId, nextTitle);
    },
    [meetingId, onSave, title],
  );

  const startEditing = useCallback(() => {
    setDraft(title ?? "");
    setEditing(true);
  }, [title]);

  const finishEditing = useCallback(
    (save: boolean) => {
      if (save) commitSave(draft);
      else setDraft(title ?? "");
      setEditing(false);
    },
    [commitSave, draft, title],
  );

  if (editing) {
    return (
      <div className="flex min-w-0 items-center gap-2">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={(event) => {
            if (
              !event.currentTarget.parentElement?.contains(
                event.relatedTarget as Node | null,
              )
            ) {
              finishEditing(true);
            }
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              finishEditing(true);
            } else if (event.key === "Escape") {
              event.preventDefault();
              finishEditing(false);
            }
          }}
          autoFocus
          aria-label="会議タイトル"
          className="field min-w-0 flex-1 text-base font-semibold text-ink"
        />
        <Tooltip content="タイトルを保存">
          <button
            type="button"
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => finishEditing(true)}
            className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary text-white transition-colors hover:bg-primary-hover"
            aria-label="タイトルを保存"
          >
            <Check aria-hidden="true" className="size-4" />
          </button>
        </Tooltip>
        <Tooltip content="タイトル編集をやめる">
          <button
            type="button"
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => finishEditing(false)}
            className="flex size-9 shrink-0 items-center justify-center rounded-full border border-line bg-surface text-ink-muted transition-colors hover:text-ink"
            aria-label="タイトル編集をやめる"
          >
            <X aria-hidden="true" className="size-4" />
          </button>
        </Tooltip>
      </div>
    );
  }

  return (
    <div className="group flex min-w-0 items-start gap-2">
      <h2
        ref={headingRef}
        tabIndex={-1}
        className="min-w-0 break-words font-display text-xl font-semibold leading-tight text-ink"
        onClick={startEditing}
      >
        {displayTitle}
      </h2>
      <Tooltip content="タイトルを編集">
        <button
          type="button"
          onClick={startEditing}
          className="flex size-8 shrink-0 items-center justify-center rounded-full text-ink-faint transition-colors hover:bg-primary-soft hover:text-primary"
          aria-label="タイトルを編集"
        >
          <Pencil aria-hidden="true" className="size-3.5" />
        </button>
      </Tooltip>
      {saving && (
        <span
          className="inline-flex shrink-0 items-center gap-1 pt-1 text-xs text-ink-muted"
          role="status"
        >
          <RefreshCw
            aria-hidden="true"
            className="size-3 animate-spin motion-reduce:animate-none"
          />
          保存中
        </span>
      )}
    </div>
  );
}

// ── Delete Confirmation Dialog ───────────────────────────────────

function DeleteDialog({
  open,
  onConfirm,
  onCancel,
  deleting,
  restoreFocusRef,
}: {
  open: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  deleting: boolean;
  restoreFocusRef: React.RefObject<HTMLElement | null>;
}) {
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  const handleCancel = useCallback(() => {
    onCancel();
    requestAnimationFrame(() => restoreFocusRef.current?.focus());
  }, [onCancel, restoreFocusRef]);

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => !nextOpen && handleCancel()}
    >
      <DialogContent
        title="会議を削除しますか？"
        description="この操作は元に戻せません。データベースと録音ファイルが完全に削除されます。"
        titleId="delete-dialog-title"
        descriptionId="delete-dialog-desc"
        showClose={false}
        onCloseAutoFocus={(event) => event.preventDefault()}
        className="max-w-[26rem]"
      >
        <div className="p-6">
          <p className="text-xs font-semibold text-danger">
            この操作は取り消せません。
          </p>
          <div className="mt-6 flex flex-wrap items-center justify-end gap-3">
            <DialogClose
              ref={cancelButtonRef}
              type="button"
              disabled={deleting}
              autoFocus
              className="min-h-10 rounded-full border border-line bg-surface px-4 py-2 text-xs font-semibold text-ink-muted transition-colors hover:border-line-strong hover:text-ink disabled:opacity-40"
            >
              キャンセル
            </DialogClose>
            <button
              type="button"
              onClick={onConfirm}
              disabled={deleting}
              className="inline-flex min-h-10 items-center gap-2 rounded-full bg-danger px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-danger/90 disabled:opacity-40"
            >
              {deleting && (
                <RefreshCw
                  aria-hidden="true"
                  className="size-3.5 animate-spin motion-reduce:animate-none"
                />
              )}
              削除する
            </button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ── Conversation / Cue Timeline ──────────────────────────────────

function CueCards({
  suggestions,
  startedAt,
}: {
  suggestions: ReplySuggestionItem[];
  startedAt: string;
}) {
  if (suggestions.length === 0) return null;

  return (
    <div className="mt-3 space-y-2 border-l-2 border-cue-soft pl-3">
      {suggestions.map((suggestion) => {
        const relativeTime = formatRelativeTime(
          suggestion.created_at,
          startedAt,
        );
        return (
          <article
            key={suggestion.id}
            className="rounded-xl border border-cue/25 bg-cue-soft/55 p-3"
          >
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <span className="inline-flex items-center gap-1.5 text-xs font-bold tracking-[0.14em] text-cue">
                <MessageSquareReply aria-hidden="true" className="size-3.5" />
                返答案
              </span>
              <span className="flex items-center gap-2 text-xs text-ink-muted">
                <span>スタイル: {suggestion.agent_label}</span>
                {relativeTime && (
                  <time dateTime={suggestion.created_at ?? undefined}>
                    {relativeTime}
                  </time>
                )}
              </span>
            </div>
            <p className="whitespace-pre-wrap text-sm font-medium leading-6 text-ink">
              {suggestion.text}
            </p>
          </article>
        );
      })}
    </div>
  );
}

function ConversationTimeline({
  turns,
  suggestions,
  startedAt,
}: {
  turns: TurnItem[] | undefined;
  suggestions: ReplySuggestionItem[] | undefined;
  startedAt: string;
}) {
  const orderedTurns = [...(turns ?? [])].sort(
    (a, b) => a.sequence - b.sequence,
  );
  const orderedSuggestions = [...(suggestions ?? [])].sort(
    (a, b) => a.sequence - b.sequence,
  );
  const suggestionsByTurn = new Map<string, ReplySuggestionItem[]>();

  for (const suggestion of orderedSuggestions) {
    const group = suggestionsByTurn.get(suggestion.target_turn_id) ?? [];
    group.push(suggestion);
    suggestionsByTurn.set(suggestion.target_turn_id, group);
  }

  const knownTurnIds = new Set(orderedTurns.map((turn) => turn.id));
  const unlinkedSuggestions = orderedSuggestions.filter(
    (suggestion) => !knownTurnIds.has(suggestion.target_turn_id),
  );

  if (orderedTurns.length === 0 && orderedSuggestions.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-line-strong bg-surface px-5 py-10 text-center">
        <MessageSquareReply
          aria-hidden="true"
          className="mx-auto size-5 text-ink-faint"
        />
        <p className="mt-3 text-sm font-semibold text-ink">
          会話の記録はありません
        </p>
        <p className="mt-1 text-xs text-ink-muted">
          音声が認識されると、発言と返答案がここに並びます。
        </p>
      </div>
    );
  }

  return (
    <ol className="space-y-1" aria-label="会話と返答案の時間軸">
      {orderedTurns.map((turn, index) => {
        const isOther = turn.speaker === "other";
        const relativeTime = formatRelativeTime(turn.created_at, startedAt);
        const cueCards = suggestionsByTurn.get(turn.id) ?? [];
        return (
          <li
            key={turn.id}
            className="grid min-w-0 grid-cols-[3rem_minmax(0,1fr)] gap-3"
          >
            <div className="pt-1 text-right text-xs tabular-nums text-ink-faint">
              {relativeTime ? (
                <time dateTime={turn.created_at ?? undefined}>
                  {relativeTime}
                </time>
              ) : (
                <span aria-label={`${index + 1}番目の発言`}>
                  {String(index + 1).padStart(2, "0")}
                </span>
              )}
            </div>
            <div className="relative min-w-0 border-l border-line pb-5 pl-4">
              <span
                className={`absolute -left-[5px] top-1.5 size-2.5 rounded-full border-2 border-surface ${isOther ? "bg-cue" : "bg-positive"}`}
              />
              <div
                className={`mb-1 text-xs font-semibold ${isOther ? "text-cue" : "text-positive"}`}
              >
                {isOther ? "相手" : "自分"}
              </div>
              <div className="rounded-xl border border-line bg-surface px-4 py-3 text-sm leading-6 text-ink shadow-card">
                {turn.text}
              </div>
              <CueCards suggestions={cueCards} startedAt={startedAt} />
            </div>
          </li>
        );
      })}
      {unlinkedSuggestions.length > 0 && (
        <li className="grid min-w-0 grid-cols-[3rem_minmax(0,1fr)] gap-3">
          <div />
          <div className="min-w-0 border-l border-line pb-2 pl-4">
            <p className="mb-2 text-xs font-semibold text-ink-muted">
              保存された返答案
            </p>
            <CueCards suggestions={unlinkedSuggestions} startedAt={startedAt} />
          </div>
        </li>
      )}
    </ol>
  );
}

function MinutesSection({
  meeting,
  routeStatus,
  status = "idle",
  progress = "",
  error = null,
  onGenerate,
  onCancel,
  onSettings,
}: {
  meeting: MeetingDetail;
  routeStatus: AiUseCaseRouteStatus;
  status?: "idle" | "generating" | "cancelled" | "error";
  progress?: string;
  error?: string | null;
  onGenerate?: (id: string) => Promise<void>;
  onCancel?: () => void;
  onSettings?: () => void;
}) {
  const eligible =
    meeting.status === "completed" && (meeting.turns?.length ?? 0) > 0;
  if (!eligible) return null;

  const generating = status === "generating";
  const ready = routeStatus.canGenerate;
  const routeStatusLoading =
    routeStatus.readiness === "unknown" && routeStatus.message === null;
  return (
    <section
      aria-labelledby="minutes-heading"
      className="rounded-2xl border border-line bg-surface p-5 shadow-card"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-bold tracking-[0.14em] text-primary">
            POST-MEETING
          </p>
          <h3
            id="minutes-heading"
            className="font-display text-lg font-semibold text-ink"
          >
            要約・議事録
          </h3>
          {!ready && !generating && (
            <div className="mt-1 space-y-1 text-xs leading-5 text-ink-muted">
              <p>AIの準備を確認してから作成できます。</p>
              <p role="status">
                {routeStatus.message ??
                  (routeStatusLoading
                    ? "支援方法の状態を確認しています。"
                    : "議事録を利用する支援方法を確認してください。")}
              </p>
              {!routeStatusLoading && onSettings && (
                <button
                  type="button"
                  onClick={onSettings}
                  className="font-semibold text-primary underline underline-offset-2"
                >
                  設定を確認
                </button>
              )}
            </div>
          )}
        </div>
        {generating ? (
          <button
            type="button"
            onClick={onCancel}
            aria-label="生成を中止"
            className="min-h-10 rounded-full border border-danger/30 bg-surface px-4 py-2 text-xs font-semibold text-danger"
          >
            生成を中止
          </button>
        ) : (
          <button
            type="button"
            disabled={!ready}
            onClick={() => {
              if (ready && onGenerate) void onGenerate(meeting.id);
            }}
            aria-label="議事録を生成"
            className="min-h-10 rounded-full bg-primary px-4 py-2 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {meeting.minutes ? "要約・議事録を作り直す" : "要約・議事録を作成"}
          </button>
        )}
      </div>
      {generating && (
        <p role="status" className="mt-3 text-xs text-ink-muted">
          要約・議事録を作成しています…
        </p>
      )}
      {progress && (
        <div className="mt-3 space-y-1 text-sm leading-6 text-ink">
          {progress
            .split("\n")
            .filter(Boolean)
            .map((line, index) => (
              <p key={`${index}-${line}`}>
                {line.replace(/^(?:#+\s*|-\s*)/, "")}
              </p>
            ))}
        </div>
      )}
      {status === "cancelled" && (
        <p role="status" className="mt-3 text-xs text-ink-muted">
          生成を停止しました。途中の内容は保存されていません。
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 text-xs text-danger">
          {error}
        </p>
      )}
      {meeting.minutes && !generating && (
        <div className="mt-4 space-y-1 rounded-xl bg-paper p-4 text-sm leading-6 text-ink">
          {meeting.minutes
            .split("\n")
            .filter(Boolean)
            .map((line, index) => (
              <p key={`${index}-${line}`}>
                {line.replace(/^(?:#+\s*|-\s*)/, "")}
              </p>
            ))}
        </div>
      )}
    </section>
  );
}

// ── Main Component ───────────────────────────────────────────────

export function MeetingHistoryDetail({
  meeting,
  loadingDetail,
  saving,
  deleting,
  error,
  onRetry,
  onUpdateTitle,
  onDelete,
  minutesStatus = "idle",
  minutesProgress = "",
  minutesError = null,
  minutesRouteStatus = {
    readiness: "setup_required",
    canGenerate: false,
    message: "議事録を利用する支援方法を設定してください。",
  },
  onGenerateMinutes,
  onCancelMinutes,
  onSettings,
}: Props) {
  const [deleteOpen, setDeleteOpen] = useState(false);
  const deleteButtonRef = useRef<HTMLButtonElement>(null);
  const turns = meeting.turns ?? [];
  const replySuggestions = meeting.reply_suggestions ?? [];
  const recordingAssets = meeting.recording_assets ?? [];

  if (loadingDetail) {
    return (
      <div
        aria-label="会議の内容を読み込み中"
        aria-busy="true"
        className="space-y-4 p-6"
      >
        <span className="sr-only">会議の内容を読み込んでいます</span>
        {Array.from({ length: 5 }).map((_, index) => (
          <div
            key={index}
            className="rounded-xl border border-line bg-surface p-4"
          >
            <div className="skeleton mb-3 h-4 w-1/2" />
            <div className="skeleton h-3 w-full" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-4xl min-w-0 space-y-5 p-4 sm:p-6">
      <header className="rounded-2xl border border-line bg-surface p-5 shadow-card">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <InlineTitleEditor
              title={meeting.title}
              meetingId={meeting.id}
              onSave={onUpdateTitle}
              saving={saving}
            />
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-muted">
              <span className="inline-flex items-center gap-1.5">
                <CalendarDays aria-hidden="true" className="size-3.5" />
                {formatDate(meeting.started_at)}
              </span>
              <span className="inline-flex items-center gap-1.5">
                <Clock3 aria-hidden="true" className="size-3.5" />
                {formatDuration(meeting.duration_seconds)}
              </span>
              <span
                className={`inline-flex rounded-full px-2 py-0.5 font-semibold ${statusColor(meeting.status)}`}
              >
                {statusLabel(meeting.status)}
              </span>
            </div>
          </div>
          <Tooltip content="会議を削除">
            <button
              ref={deleteButtonRef}
              type="button"
              onClick={() => setDeleteOpen(true)}
              className="flex size-9 shrink-0 items-center justify-center rounded-full text-ink-faint transition-colors hover:bg-danger-soft hover:text-danger"
              aria-label="削除"
            >
              <Trash2 aria-hidden="true" className="size-4" />
            </button>
          </Tooltip>
        </div>
      </header>

      <DeleteDialog
        open={deleteOpen}
        onConfirm={() => {
          void onDelete(meeting.id);
          setDeleteOpen(false);
        }}
        onCancel={() => setDeleteOpen(false)}
        deleting={deleting}
        restoreFocusRef={deleteButtonRef}
      />

      {error && (
        <div
          role="alert"
          className="flex min-w-0 flex-wrap items-center gap-3 rounded-xl border border-danger/25 bg-danger-soft px-4 py-3 text-xs text-danger"
        >
          <AlertCircle aria-hidden="true" className="size-4 shrink-0" />
          <p className="min-w-0 flex-1 leading-5">{error}</p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex min-h-8 items-center gap-1.5 rounded-full border border-danger/30 bg-surface px-3 py-1.5 font-semibold transition-colors hover:border-danger"
            >
              <RefreshCw aria-hidden="true" className="size-3.5" />
              表示を更新
            </button>
          )}
        </div>
      )}

      <section aria-labelledby="conversation-timeline-heading">
        <div className="mb-3 flex items-end justify-between gap-4">
          <div>
            <p className="text-xs font-bold tracking-[0.14em] text-primary">
              REVIEW
            </p>
            <h3
              id="conversation-timeline-heading"
              className="font-display text-lg font-semibold text-ink"
            >
              会話と返答案
            </h3>
          </div>
          <span className="text-xs text-ink-faint">開始からの流れ</span>
        </div>
        <ConversationTimeline
          turns={turns}
          suggestions={replySuggestions}
          startedAt={meeting.started_at}
        />
      </section>

      <MinutesSection
        meeting={meeting}
        routeStatus={minutesRouteStatus}
        status={minutesStatus}
        progress={minutesProgress}
        error={minutesError}
        onGenerate={onGenerateMinutes}
        onCancel={onCancelMinutes}
        onSettings={onSettings}
      />

      <section aria-labelledby="recording-heading">
        <div className="mb-3 flex items-center gap-2">
          <Mic2 aria-hidden="true" className="size-4 text-primary" />
          <h3
            id="recording-heading"
            className="font-display text-lg font-semibold text-ink"
          >
            録音を聴く
          </h3>
        </div>
        <RecordingPlayer meetingId={meeting.id} recordings={recordingAssets} />
      </section>
    </div>
  );
}
