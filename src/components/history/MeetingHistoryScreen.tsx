import { useCallback, useEffect, useRef, useState } from "react";
import { useMeetingHistoryStore } from "../../store/meetingHistoryStore";
import { MeetingHistoryList } from "./MeetingHistoryList";
import { MeetingHistoryDetail } from "./MeetingHistoryDetail";
import type { AiUseCaseRouteStatus } from "../../hooks/useAiRoutes";
import {
  ArrowLeft,
  ChevronLeft,
  MessageSquareText,
  RefreshCw,
} from "lucide-react";

interface Props {
  onBack: () => void;
  minutesRouteStatus: AiUseCaseRouteStatus;
  onSettings: () => void;
}

export function MeetingHistoryScreen({
  onBack,
  minutesRouteStatus,
  onSettings,
}: Props) {
  const {
    meetings,
    selectedMeetingId,
    selectedMeeting,
    loading,
    loadingDetail,
    error,
    hasMore,
    loadingMore,
    saving,
    deleting,
    minutesStatus,
    minutesProgress,
    minutesError,
    loadMeetings,
    loadMore,
    selectMeeting,
    updateTitle,
    deleteMeeting,
    generateMinutes,
    cancelMinutes,
  } = useMeetingHistoryStore();
  const [compactDetailOpen, setCompactDetailOpen] = useState(false);
  const listContainerRef = useRef<HTMLElement>(null);
  const backButtonRef = useRef<HTMLButtonElement>(null);

  const focusSelectedListItem = useCallback(() => {
    requestAnimationFrame(() => {
      const selectedId = useMeetingHistoryStore.getState().selectedMeetingId;
      const target = Array.from(
        listContainerRef.current?.querySelectorAll<HTMLButtonElement>(
          "[data-meeting-id]",
        ) ?? [],
      ).find((button) => button.dataset.meetingId === selectedId);
      (target ?? listContainerRef.current)?.focus();
    });
  }, []);

  const handleSelect = useCallback(
    (id: string) => {
      setCompactDetailOpen(true);
      void selectMeeting(id);
    },
    [selectMeeting],
  );

  const handleShowList = useCallback(() => {
    setCompactDetailOpen(false);
    focusSelectedListItem();
  }, [focusSelectedListItem]);

  const handleDelete = useCallback(
    async (id: string) => {
      await deleteMeeting(id);

      const { error: deleteError } = useMeetingHistoryStore.getState();
      if (deleteError) return;

      setCompactDetailOpen(false);
      requestAnimationFrame(() => {
        const { selectedMeetingId: nextId, meetings: nextMeetings } =
          useMeetingHistoryStore.getState();

        if (nextId) {
          const target = Array.from(
            listContainerRef.current?.querySelectorAll<HTMLButtonElement>(
              "[data-meeting-id]",
            ) ?? [],
          ).find((button) => button.dataset.meetingId === nextId);
          (target ?? listContainerRef.current)?.focus();
        } else if (nextMeetings.length > 0) {
          listContainerRef.current?.focus();
        } else {
          backButtonRef.current?.focus();
        }
      });
    },
    [deleteMeeting],
  );

  const handleGenerateMinutes = useCallback(
    async (id: string) => {
      if (!minutesRouteStatus.canGenerate) return;
      await generateMinutes(id);
    },
    [generateMinutes, minutesRouteStatus.canGenerate],
  );

  useEffect(() => {
    void loadMeetings();
  }, [loadMeetings]);


  const listError = error?.startsWith("履歴") ? error : null;

  return (
    <div
      data-testid="meeting-history-screen"
      className="flex min-w-0 flex-1 flex-col overflow-hidden bg-paper"
    >
      <header className="flex shrink-0 items-center gap-3 border-b border-line bg-surface px-4 py-3">
        <button
          ref={backButtonRef}
          type="button"
          onClick={onBack}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold text-ink-muted transition-colors hover:bg-primary-soft hover:text-primary"
          aria-label="会議履歴を閉じて戻る"
        >
          <ArrowLeft aria-hidden="true" className="size-4" />
          戻る
        </button>
        <div className="min-w-0">
          <h1 className="font-display text-lg font-semibold tracking-wide text-ink">
            会議のふりかえり
          </h1>
          <p className="hidden text-xs text-ink-muted min-[840px]:block">
            会話と返答案を、時間の流れに沿って確認できます
          </p>
        </div>
      </header>

      <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden">
        <aside
          ref={listContainerRef}
          tabIndex={-1}
          aria-label="会議の一覧"
          className={`${compactDetailOpen ? "hidden" : "block"} min-w-0 flex-1 overflow-y-auto bg-paper min-[840px]:block min-[840px]:w-[300px] min-[840px]:flex-none min-[840px]:border-r min-[840px]:border-line`}
        >
          <MeetingHistoryList
            meetings={meetings}
            selectedId={selectedMeetingId}
            onSelect={handleSelect}
            loading={loading}
            error={listError}
            hasMore={hasMore}
            loadingMore={loadingMore}
            onLoadMore={loadMore}
            onRetry={loadMeetings}
            onEmptyAction={onBack}
          />
        </aside>

        <main
          className={`${compactDetailOpen ? "flex" : "hidden"} min-w-0 flex-1 flex-col overflow-y-auto bg-paper min-[840px]:flex`}
        >
          <div className="sticky top-0 z-10 border-b border-line bg-surface px-4 py-2 min-[840px]:hidden">
            <button
              type="button"
              onClick={handleShowList}
              className="inline-flex min-h-9 items-center gap-1 rounded-full px-3 py-1.5 text-xs font-semibold text-primary transition-colors hover:bg-primary-soft"
            >
              <ChevronLeft aria-hidden="true" className="size-4" />
              会議一覧
            </button>
          </div>

          {!selectedMeetingId ? (
            <div className="flex flex-1 items-center justify-center p-8">
              <div className="max-w-sm text-center">
                <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-full border border-line bg-surface text-primary">
                  <MessageSquareText aria-hidden="true" className="size-5" />
                </div>
                <p className="text-sm font-semibold text-ink">
                  ふりかえる会議を選んでください
                </p>
                <p className="mt-2 text-xs leading-5 text-ink-muted">
                  一覧から選ぶと、会話とそのときの返答案が表示されます。
                </p>
              </div>
            </div>
          ) : selectedMeeting?.id !== selectedMeetingId ? (
            loadingDetail ? (
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
            ) : (
              <div
                role="alert"
                className="flex flex-1 items-center justify-center p-8"
              >
                <div className="max-w-sm text-center">
                  <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-full bg-danger-soft text-danger">
                    <RefreshCw aria-hidden="true" className="size-5" />
                  </div>
                  <p className="text-sm font-semibold text-ink">
                    会議の内容を表示できませんでした
                  </p>
                  {error && (
                    <p className="mt-2 text-xs leading-5 text-ink-muted">
                      {error}
                    </p>
                  )}
                  <button
                    type="button"
                    onClick={() => void selectMeeting(selectedMeetingId)}
                    className="mt-5 inline-flex min-h-10 items-center gap-2 rounded-full bg-primary px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-primary-hover"
                  >
                    <RefreshCw aria-hidden="true" className="size-4" />
                    もう一度読み込む
                  </button>
                </div>
              </div>
            )
          ) : (
            <MeetingHistoryDetail
              meeting={selectedMeeting}
              loadingDetail={loadingDetail}
              saving={saving}
              deleting={deleting}
              error={listError ? null : error}
              onRetry={() => void selectMeeting(selectedMeeting.id)}
              onUpdateTitle={updateTitle}
              onDelete={handleDelete}
              minutesStatus={minutesStatus}
              minutesProgress={minutesProgress}
              minutesError={minutesError}
              minutesRouteStatus={minutesRouteStatus}
              onGenerateMinutes={handleGenerateMinutes}
              onCancelMinutes={cancelMinutes}
              onSettings={onSettings}
            />
          )}
        </main>
      </div>
    </div>
  );
}
