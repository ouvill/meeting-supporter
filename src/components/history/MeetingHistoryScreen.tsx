import { useCallback, useEffect, useRef, useState } from "react";
import { useMeetingHistoryStore } from "../../store/meetingHistoryStore";
import { MeetingHistoryList } from "./MeetingHistoryList";
import { MeetingHistoryDetail } from "./MeetingHistoryDetail";
import { ChevronLeft, RefreshCw } from "lucide-react";
import { Button } from "../ui/Button";

interface Props {
  onBack: () => void;
}

export function MeetingHistoryScreen({ onBack }: Props) {
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
    loadMeetings,
    loadMore,
    selectMeeting,
    updateTitle,
    deleteMeeting,
  } = useMeetingHistoryStore();
  const [compactDetailOpen, setCompactDetailOpen] = useState(false);
  const listContainerRef = useRef<HTMLElement>(null);

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
        const { selectedMeetingId: nextId } = useMeetingHistoryStore.getState();

        if (nextId) {
          const target = Array.from(
            listContainerRef.current?.querySelectorAll<HTMLButtonElement>(
              "[data-meeting-id]",
            ) ?? [],
          ).find((button) => button.dataset.meetingId === nextId);
          (target ?? listContainerRef.current)?.focus();
        } else {
          listContainerRef.current?.focus();
        }
      });
    },
    [deleteMeeting],
  );

  useEffect(() => {
    void loadMeetings();
  }, [loadMeetings]);

  const listError = error?.startsWith("履歴") ? error : null;

  return (
    <div
      data-testid="meeting-history-screen"
      className="flex min-w-0 flex-1 flex-col overflow-hidden bg-surface"
    >
      <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden">
        <aside
          ref={listContainerRef}
          tabIndex={-1}
          aria-label="会議の一覧"
          className={`${compactDetailOpen ? "hidden" : "flex"} min-w-0 flex-1 flex-col overflow-y-auto bg-paper outline-none min-[840px]:flex min-[840px]:w-[280px] min-[840px]:flex-none min-[840px]:border-r min-[840px]:border-line`}
        >
          <header className="shrink-0 px-6 pb-2 pt-6">
            <h1 className="text-[22px] font-bold tracking-tight text-ink">
              履歴
            </h1>
          </header>
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
          className={`${compactDetailOpen ? "flex" : "hidden"} min-w-0 flex-1 flex-col overflow-y-auto bg-surface min-[840px]:flex`}
        >
          <div className="sticky top-0 z-10 border-b border-line bg-surface px-3 py-2 min-[840px]:hidden">
            <button
              type="button"
              onClick={handleShowList}
              className="inline-flex min-h-9 items-center gap-1 rounded-lg px-3 py-1.5 text-sm font-semibold text-primary transition-colors hover:bg-primary-soft motion-reduce:transition-none"
            >
              <ChevronLeft aria-hidden="true" className="size-4" />
              会議一覧
            </button>
          </div>

          {!selectedMeetingId ? (
            <div className="flex flex-1 items-center justify-center p-8">
              <div className="max-w-sm text-center">
                <p className="text-sm font-semibold text-ink-muted">
                  ふりかえる会議を選んでください
                </p>
                <p className="mt-2 text-xs leading-5 text-ink-faint">
                  一覧から選ぶと、会話とそのときの返答案が表示されます。
                </p>
              </div>
            </div>
          ) : selectedMeeting?.id !== selectedMeetingId ? (
            loadingDetail ? (
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
            ) : (
              <div
                role="alert"
                className="flex flex-1 items-center justify-center p-8"
              >
                <div className="max-w-sm text-center">
                  <p className="text-sm font-semibold text-ink">
                    会議の内容を表示できませんでした
                  </p>
                  {error && (
                    <p className="mt-2 text-xs leading-5 text-ink-muted">
                      {error}
                    </p>
                  )}
                  <Button
                    variant="secondary"
                    onClick={() => void selectMeeting(selectedMeetingId)}
                    className="mt-5"
                  >
                    <RefreshCw aria-hidden="true" className="size-4" />
                    もう一度読み込む
                  </Button>
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
            />
          )}
        </main>
      </div>
    </div>
  );
}
