import { useCallback, useEffect, useRef, useState } from "react";
import { Dialog, DialogClose, DialogContent } from "../ui/Dialog";
import { Tooltip } from "../ui/Tooltip";
import { Check, Pencil, RefreshCw, Sparkles, Trash2, X } from "lucide-react";
import { Button } from "../ui/Button";
import { InlineNotice } from "../ui/InlineNotice";
import type {
  MeetingDetail,
  ReplySuggestionItem,
  TurnItem,
} from "../../api/generated/types.gen";
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
        className="min-w-0 break-words font-display text-[22px] font-bold leading-tight tracking-tight text-ink outline-none"
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
    <div className="mt-1.5 w-full max-w-[88%] space-y-1.5 self-start">
      {suggestions.map((suggestion) => {
        const relativeTime = formatRelativeTime(
          suggestion.created_at,
          startedAt,
        );
        return (
          <article
            key={suggestion.id}
            className="rounded-xl border border-primary/25 bg-surface px-3.5 py-2.5"
          >
            <div className="mb-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
              <span className="inline-flex items-center gap-1.5 font-bold text-primary">
                <Sparkles aria-hidden="true" className="size-3" />
                返答案
              </span>
              <span className="text-ink-muted">
                スタイル: {suggestion.agent_label}
              </span>
              {relativeTime && (
                <time
                  dateTime={suggestion.created_at ?? undefined}
                  className="ml-auto tabular-nums text-ink-faint"
                >
                  {relativeTime}
                </time>
              )}
            </div>
            <p className="whitespace-pre-wrap text-sm leading-6 text-ink">
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
      <div className="py-8 text-center">
        <p className="text-sm font-semibold text-ink-muted">
          会話の記録はありません
        </p>
        <p className="mt-1 text-xs text-ink-faint">
          音声が認識されると、発言と返答案がここに並びます。
        </p>
      </div>
    );
  }

  return (
    <ol className="flex flex-col" aria-label="会話と返答案の時間軸">
      {orderedTurns.map((turn, index) => {
        const isOther = turn.speaker === "other";
        const relativeTime = formatRelativeTime(turn.created_at, startedAt);
        const cueCards = suggestionsByTurn.get(turn.id) ?? [];
        const previous = orderedTurns[index - 1];
        // A reply shown under the previous turn breaks the run of one speaker.
        const continued =
          previous?.speaker === turn.speaker &&
          !suggestionsByTurn.has(previous.id);
        return (
          <li
            key={turn.id}
            className={`flex min-w-0 flex-col ${isOther ? "items-start" : "items-end"} ${index === 0 ? "" : continued ? "mt-1" : "mt-4"}`}
          >
            <div
              className={`mb-1 flex items-baseline gap-2 px-1 text-xs ${continued ? "sr-only" : ""}`}
            >
              <span
                className={`font-bold ${isOther ? "text-ink-muted" : "text-primary"}`}
              >
                {isOther ? "相手" : "自分"}
              </span>
              {relativeTime ? (
                <time
                  dateTime={turn.created_at ?? undefined}
                  className="tabular-nums text-ink-faint"
                >
                  {relativeTime}
                </time>
              ) : (
                <span className="sr-only">{index + 1}番目の発言</span>
              )}
            </div>
            <div
              className={`max-w-[88%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm leading-6 text-ink ${isOther ? "rounded-tl-md bg-surface-muted" : "rounded-tr-md bg-primary-soft"}`}
            >
              {turn.text}
            </div>
            <CueCards suggestions={cueCards} startedAt={startedAt} />
          </li>
        );
      })}
      {unlinkedSuggestions.length > 0 && (
        <li className="mt-4 flex min-w-0 flex-col">
          <p className="mb-1 px-1 text-xs font-bold text-ink-muted">
            保存された返答案
          </p>
          <CueCards suggestions={unlinkedSuggestions} startedAt={startedAt} />
        </li>
      )}
    </ol>
  );
}

function MinutesSection({ meeting }: { meeting: MeetingDetail }) {
  if (!meeting.minutes) return null;
  return (
    <section aria-labelledby="minutes-heading">
      <h3 id="minutes-heading" className="text-sm font-bold text-ink">
        保存済みの議事録
      </h3>
      <div className="mt-3 whitespace-pre-wrap text-sm leading-7 text-ink">
        {meeting.minutes}
      </div>
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
        className="mx-auto w-full max-w-3xl space-y-6 px-6 py-8 sm:px-10"
      >
        <span className="sr-only">会議の内容を読み込んでいます</span>
        {Array.from({ length: 5 }).map((_, index) => (
          <div key={index}>
            <div className="skeleton mb-3 h-4 w-1/2" />
            <div className="skeleton h-3 w-full" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="mx-auto w-full min-w-0 max-w-3xl space-y-7 px-6 pb-10 pt-6 sm:px-10">
      <header>
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <InlineTitleEditor
              title={meeting.title}
              meetingId={meeting.id}
              onSave={onUpdateTitle}
              saving={saving}
            />
            <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-sm text-ink-muted">
              <span>{formatDate(meeting.started_at)}</span>
              <span aria-hidden="true" className="text-ink-faint">
                ·
              </span>
              <span>{formatDuration(meeting.duration_seconds)}</span>
              {meeting.status === "aborted" && (
                <span className="inline-flex rounded-full bg-warning-soft px-2 py-0.5 text-xs font-semibold text-warning">
                  中断
                </span>
              )}
            </div>
          </div>
          <Tooltip content="会議を削除">
            <button
              ref={deleteButtonRef}
              type="button"
              onClick={() => setDeleteOpen(true)}
              className="flex size-9 shrink-0 items-center justify-center rounded-lg text-ink-faint transition-colors hover:bg-danger-soft hover:text-danger motion-reduce:transition-none"
              aria-label="削除"
            >
              <Trash2 aria-hidden="true" className="size-4" />
            </button>
          </Tooltip>
        </div>
      </header>

      {meeting.status === "aborted" && (
        <InlineNotice tone="warning">
          この会議は正常に終了しませんでした。保存できた記録を表示しています。
          文字起こしや録音が欠けている可能性があります。
        </InlineNotice>
      )}

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
        <InlineNotice
          tone="danger"
          action={
            onRetry && (
              <Button variant="secondary" size="sm" onClick={onRetry}>
                <RefreshCw aria-hidden="true" className="size-3.5" />
                表示を更新
              </Button>
            )
          }
        >
          {error}
        </InlineNotice>
      )}

      <section aria-labelledby="recording-heading">
        <h3
          id="recording-heading"
          className="mb-2.5 text-sm font-bold text-ink"
        >
          録音
        </h3>
        <RecordingPlayer meetingId={meeting.id} recordings={recordingAssets} />
      </section>

      <section aria-labelledby="conversation-timeline-heading">
        <div className="mb-3 flex items-baseline justify-between gap-4">
          <h3
            id="conversation-timeline-heading"
            className="text-sm font-bold text-ink"
          >
            会話と返答案
          </h3>
          <span className="text-xs text-ink-faint">
            時刻は会議開始からの経過
          </span>
        </div>
        <ConversationTimeline
          turns={turns}
          suggestions={replySuggestions}
          startedAt={meeting.started_at}
        />
      </section>

      <MinutesSection meeting={meeting} />
    </div>
  );
}
