import type { MeetingListItem } from "../../api/generated/types.gen";
import {
  ArrowRight,
  CalendarDays,
  Clock3,
  History,
  Mic2,
  RefreshCw,
} from "lucide-react";

interface Props {
  meetings: MeetingListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onRetry?: () => void;
  onEmptyAction?: () => void;
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
  if (m < 1) return "1分未満";
  return `${m}分`;
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

// ── Component ────────────────────────────────────────────────────

export function MeetingHistoryList({
  meetings,
  selectedId,
  onSelect,
  loading,
  error,
  hasMore,
  loadingMore,
  onLoadMore,
  onRetry,
  onEmptyAction,
}: Props) {
  if (loading) {
    return (
      <section
        aria-label="会議履歴を読み込み中"
        aria-busy="true"
        className="space-y-3 p-4"
      >
        <span className="sr-only">会議履歴を読み込んでいます</span>
        {Array.from({ length: 5 }).map((_, index) => (
          <div
            key={index}
            className="rounded-xl border border-line bg-surface p-4"
          >
            <div className="skeleton mb-3 h-4 w-3/4" />
            <div className="skeleton h-3 w-1/2" />
          </div>
        ))}
      </section>
    );
  }

  if (error && meetings.length === 0) {
    return (
      <section
        role="alert"
        className="flex min-h-72 flex-col items-center justify-center px-8 py-12 text-center"
      >
        <div className="mb-4 flex size-11 items-center justify-center rounded-full bg-danger-soft text-danger">
          <RefreshCw aria-hidden="true" className="size-5" />
        </div>
        <h2 className="text-base font-semibold text-ink">
          履歴を表示できませんでした
        </h2>
        <p className="mt-2 max-w-64 text-xs leading-5 text-ink-muted">
          {error}
        </p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="mt-5 inline-flex min-h-10 items-center gap-2 rounded-full bg-primary px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-primary-hover"
          >
            <RefreshCw aria-hidden="true" className="size-4" />
            もう一度読み込む
          </button>
        )}
      </section>
    );
  }

  if (meetings.length === 0) {
    return (
      <section className="flex min-h-72 flex-col items-center justify-center px-8 py-12 text-center">
        <div className="mb-4 flex size-12 items-center justify-center rounded-full border border-line bg-surface text-primary">
          <History aria-hidden="true" className="size-5" />
        </div>
        <h2 className="text-base font-semibold text-ink">
          会議履歴がありません
        </h2>
        <p className="mt-2 max-w-64 text-xs leading-5 text-ink-muted">
          会議を終えると、会話と返答案をここでふりかえれます。
        </p>
        {onEmptyAction && (
          <button
            type="button"
            onClick={onEmptyAction}
            className="mt-5 inline-flex min-h-10 items-center gap-2 rounded-full border border-line-strong bg-surface px-4 py-2 text-xs font-semibold text-ink transition-colors hover:border-primary hover:text-primary"
          >
            会議画面へ戻る
            <ArrowRight aria-hidden="true" className="size-4" />
          </button>
        )}
      </section>
    );
  }

  return (
    <div className="min-w-0 p-3">
      {error && (
        <div
          role="alert"
          className="mb-3 rounded-xl border border-danger/25 bg-danger-soft p-3 text-xs leading-5 text-danger"
        >
          <p>{error}</p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-2 inline-flex min-h-8 items-center gap-1.5 rounded-full border border-danger/30 bg-surface px-3 py-1.5 font-semibold transition-colors hover:border-danger"
            >
              <RefreshCw aria-hidden="true" className="size-3.5" />
              一覧を読み直す
            </button>
          )}
        </div>
      )}
      <ul className="space-y-2" aria-label="会議履歴">
        {meetings.map((meeting) => {
          const isSelected = meeting.id === selectedId;
          return (
            <li key={meeting.id}>
              <button
                type="button"
                data-meeting-id={meeting.id}
                onClick={() => onSelect(meeting.id)}
                className={`w-full min-w-0 rounded-xl border px-4 py-3.5 text-left transition-colors ${
                  isSelected
                    ? "border-primary bg-primary-soft"
                    : "border-line bg-surface hover:border-line-strong hover:bg-paper"
                }`}
                {...(isSelected ? { "aria-current": "page" as const } : {})}
              >
                <span className="block truncate text-sm font-semibold text-ink">
                  {meeting.title || "タイトル未設定"}
                </span>

                <span className="mt-2 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
                  <span className="inline-flex min-w-0 items-center gap-1.5">
                    <CalendarDays
                      aria-hidden="true"
                      className="size-3.5 shrink-0"
                    />
                    <span className="truncate">
                      {formatDate(meeting.started_at)}
                    </span>
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <Clock3 aria-hidden="true" className="size-3.5 shrink-0" />
                    {formatDuration(meeting.duration_seconds)}
                  </span>
                </span>

                <span className="mt-2.5 flex items-center gap-2">
                  <span
                    className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${statusColor(meeting.status)}`}
                  >
                    {statusLabel(meeting.status)}
                  </span>
                  {meeting.has_recording && (
                    <span
                      className="inline-flex items-center gap-1 text-xs font-medium text-primary"
                      aria-label="録音ファイルあり"
                      title="録音ファイルあり"
                    >
                      <Mic2 aria-hidden="true" className="size-3.5" />
                      録音
                    </span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {hasMore && (
        <div className="pt-3">
          <button
            type="button"
            onClick={onLoadMore}
            disabled={loadingMore}
            className="inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-xl border border-line bg-surface px-3 py-2 text-xs font-semibold text-ink-muted transition-colors hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loadingMore && (
              <RefreshCw
                aria-hidden="true"
                className="size-3.5 animate-spin motion-reduce:animate-none"
              />
            )}
            {loadingMore ? "読み込み中..." : "さらに表示"}
          </button>
        </div>
      )}
    </div>
  );
}
