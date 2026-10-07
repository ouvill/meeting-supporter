import type { MeetingListItem } from "../../api/generated/types.gen";
import { History, Mic2, Plus, RefreshCw } from "lucide-react";
import { Button } from "../ui/Button";

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

const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

function dayKey(date: Date): string {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function formatDay(iso: string, now: Date): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "日付不明";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (dayKey(d) === dayKey(now)) return "今日";
  if (dayKey(d) === dayKey(yesterday)) return "昨日";
  const day = `${d.getMonth() + 1}月${d.getDate()}日（${WEEKDAYS[d.getDay()]}）`;
  return d.getFullYear() === now.getFullYear()
    ? day
    : `${d.getFullYear()}年${day}`;
}

function formatClock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--";
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "--";
  const m = Math.floor(seconds / 60);
  if (m < 1) return "1分未満";
  return `${m}分`;
}

/** Keeps the API order and starts a new group whenever the day changes. */
function groupByDay(
  meetings: MeetingListItem[],
  now: Date,
): Array<{ label: string; meetings: MeetingListItem[] }> {
  const groups: Array<{ label: string; meetings: MeetingListItem[] }> = [];
  for (const meeting of meetings) {
    const label = formatDay(meeting.started_at, now);
    const last = groups[groups.length - 1];
    if (last?.label === label) last.meetings.push(meeting);
    else groups.push({ label, meetings: [meeting] });
  }
  return groups;
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
        className="space-y-1 p-3"
      >
        <span className="sr-only">会議履歴を読み込んでいます</span>
        {Array.from({ length: 5 }).map((_, index) => (
          <div key={index} className="px-3 py-2.5">
            <div className="skeleton mb-2.5 h-4 w-3/4" />
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
          <Button variant="secondary" onClick={onRetry} className="mt-5">
            <RefreshCw aria-hidden="true" className="size-4" />
            もう一度読み込む
          </Button>
        )}
      </section>
    );
  }

  if (meetings.length === 0) {
    return (
      <section className="flex min-h-72 flex-col items-center justify-center px-8 py-12 text-center">
        <History aria-hidden="true" className="mb-3 size-6 text-ink-faint" />
        <h2 className="text-base font-semibold text-ink">
          会議履歴がありません
        </h2>
        <p className="mt-2 max-w-64 text-xs leading-5 text-ink-muted">
          会議を終えると、会話と返答案をここでふりかえれます。
        </p>
        {onEmptyAction && (
          <Button variant="primary" onClick={onEmptyAction} className="mt-5">
            <Plus aria-hidden="true" className="size-4" />
            新しい会議を始める
          </Button>
        )}
      </section>
    );
  }

  const groups = groupByDay(meetings, new Date());

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
      <div className="space-y-4">
        {groups.map((group) => (
          <section key={`${group.label}-${group.meetings[0].id}`}>
            <h2 className="px-3 pb-1 text-xs font-bold text-ink-muted">
              {group.label}
            </h2>
            <ul className="space-y-0.5" aria-label={`${group.label}の会議`}>
              {group.meetings.map((meeting) => {
                const isSelected = meeting.id === selectedId;
                return (
                  <li key={meeting.id}>
                    <button
                      type="button"
                      data-meeting-id={meeting.id}
                      onClick={() => onSelect(meeting.id)}
                      className={`w-full min-w-0 rounded-lg px-3 py-2.5 text-left transition-colors motion-reduce:transition-none ${
                        isSelected
                          ? "bg-surface shadow-card"
                          : "hover:bg-surface-muted"
                      }`}
                      {...(isSelected
                        ? { "aria-current": "page" as const }
                        : {})}
                    >
                      <span className="block truncate text-sm font-semibold text-ink">
                        {meeting.title || "タイトル未設定"}
                      </span>
                      <span className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-muted">
                        <span className="tabular-nums">
                          {formatClock(meeting.started_at)}
                        </span>
                        <span aria-hidden="true" className="text-ink-faint">
                          ·
                        </span>
                        <span>{formatDuration(meeting.duration_seconds)}</span>
                        {meeting.has_recording && (
                          <span
                            className="inline-flex items-center gap-1"
                            aria-label="録音ファイルあり"
                            title="録音ファイルあり"
                          >
                            <Mic2 aria-hidden="true" className="size-3.5" />
                            録音
                          </span>
                        )}
                        {meeting.status === "aborted" && (
                          <span className="inline-flex rounded-full bg-warning-soft px-1.5 py-0.5 font-semibold text-warning">
                            中断
                          </span>
                        )}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>

      {hasMore && (
        <div className="pt-3">
          <button
            type="button"
            onClick={onLoadMore}
            disabled={loadingMore}
            className="inline-flex min-h-9 w-full items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold text-primary transition-colors hover:bg-primary-soft disabled:cursor-not-allowed disabled:opacity-60 motion-reduce:transition-none"
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
