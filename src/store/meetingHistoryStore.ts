import { create } from "zustand";
import {
  deleteMeetingMeetingsMeetingIdDelete,
  getMeetingMeetingsMeetingIdGet,
  listMeetingsMeetingsGet,
  updateMeetingTitleMeetingsMeetingIdPatch,
} from "../api/generated/sdk.gen";
import type {
  MeetingDetail,
  MeetingListItem,
} from "../api/generated/types.gen";
import { streamMeetingMinutes } from "../api/meetingMinutesStream";

const MEETING_HISTORY_PAGE_SIZE = 50;
// ── Public types ─────────────────────────────────────────────────

export interface MeetingHistoryState {
  meetings: MeetingListItem[];
  total: number;
  hasMore: boolean;
  selectedMeetingId: string | null;
  selectedMeeting: MeetingDetail | null;
  loading: boolean;
  loadingDetail: boolean;
  loadingMore: boolean;
  error: string | null;
  saving: boolean;
  deleting: boolean;
  minutesStatus: "idle" | "generating" | "cancelled" | "error";
  minutesProgress: string;
  minutesError: string | null;
}

export interface MeetingHistoryActions {
  loadMeetings: () => Promise<void>;
  loadMore: () => Promise<void>;
  selectMeeting: (id: string) => Promise<void>;
  updateTitle: (id: string, title: string) => Promise<void>;
  deleteMeeting: (id: string) => Promise<void>;
  reset: () => void;
  generateMinutes: (id: string) => Promise<void>;
  cancelMinutes: () => void;
}

export type MeetingHistoryStore = MeetingHistoryState & MeetingHistoryActions;

// ── Initial state ────────────────────────────────────────────────

const INITIAL: MeetingHistoryState = {
  meetings: [],
  total: 0,
  hasMore: false,
  selectedMeetingId: null,
  selectedMeeting: null,
  loading: false,
  loadingMore: false,
  loadingDetail: false,
  error: null,
  saving: false,
  deleting: false,
  minutesStatus: "idle",
  minutesProgress: "",
  minutesError: null,
};

// ── Helpers ──────────────────────────────────────────────────────

/**
 * Extract a user-facing error string from the API response error field.
 * Handles objects, strings, Error instances, null/undefined.
 */
function formatApiError(err: unknown): string {
  if (err == null) return "不明なエラー";
  if (typeof err === "string") return err;
  if (err instanceof Error) return err.message;
  if (typeof err === "object") {
    const obj = err as Record<string, unknown>;
    if (typeof obj.message === "string") return obj.message;
    if (typeof obj.detail === "string") return obj.detail;
    if (Array.isArray(obj.detail)) {
      return obj.detail
        .map((d: unknown) => {
          if (
            typeof d === "object" &&
            d &&
            typeof (d as Record<string, unknown>).msg === "string"
          ) {
            return (d as Record<string, unknown>).msg as string;
          }
          return String(d);
        })
        .join("; ");
    }
  }
  return String(err);
}

function toUserError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

let meetingListRequestGeneration = 0;
let loadMoreRequestGeneration = 0;
let meetingDetailRequestGeneration = 0;
let minutesRequestGeneration = 0;
let minutesAbortController: AbortController | null = null;

// ── Store ────────────────────────────────────────────────────────

export const useMeetingHistoryStore = create<MeetingHistoryStore>(
  (set, get) => ({
    ...INITIAL,

    loadMeetings: async () => {
      const requestGeneration = ++meetingListRequestGeneration;
      set({ loading: true, loadingMore: false, error: null });
      try {
        const res = await listMeetingsMeetingsGet({
          query: { limit: MEETING_HISTORY_PAGE_SIZE, offset: 0 },
        });
        if (requestGeneration !== meetingListRequestGeneration) return;

        if (res.error) {
          set({
            loading: false,
            error: `履歴の取得に失敗しました: ${formatApiError(res.error)}`,
          });
          return;
        }
        const page = res.data ?? {
          items: [],
          total: 0,
          limit: MEETING_HISTORY_PAGE_SIZE,
          offset: 0,
        };
        const meetings = page.items;
        const next: Partial<MeetingHistoryState> = {
          meetings,
          total: page.total,
          hasMore: meetings.length < page.total,
          loading: false,
          loadingMore: false,
          error: null,
        };

        const currentId = get().selectedMeetingId;
        const stillExists =
          currentId !== null && meetings.some((m) => m.id === currentId);

        if (stillExists) {
          // Preserve current selection, refresh detail.
          set({ ...next, selectedMeetingId: currentId });
          if (requestGeneration === meetingListRequestGeneration)
            await get().selectMeeting(currentId);
        } else if (meetings.length > 0) {
          // Select first available meeting.
          const firstId = meetings[0].id;
          set({ ...next, selectedMeetingId: firstId });
          if (requestGeneration === meetingListRequestGeneration)
            await get().selectMeeting(firstId);
        } else {
          // No meetings — clear selection and invalidate any outstanding detail response.
          ++meetingDetailRequestGeneration;
          set({
            ...next,
            selectedMeetingId: null,
            selectedMeeting: null,
            loadingDetail: false,
          });
        }
      } catch (err) {
        if (requestGeneration === meetingListRequestGeneration) {
          set({
            loading: false,
            error: `履歴の取得に失敗しました: ${toUserError(err)}`,
          });
        }
      }
    },

    loadMore: async () => {
      const { meetings, loading, loadingMore, hasMore } = get();
      if (loading || loadingMore || !hasMore) return;

      const requestGeneration = ++loadMoreRequestGeneration;
      const listGeneration = meetingListRequestGeneration;
      set({ loadingMore: true, error: null });
      try {
        const res = await listMeetingsMeetingsGet({
          query: { limit: MEETING_HISTORY_PAGE_SIZE, offset: meetings.length },
        });
        if (
          requestGeneration !== loadMoreRequestGeneration ||
          listGeneration !== meetingListRequestGeneration
        )
          return;

        if (res.error) {
          set({
            loadingMore: false,
            error: `履歴の追加取得に失敗しました: ${formatApiError(res.error)}`,
          });
          return;
        }

        const page = res.data ?? {
          items: [],
          total: meetings.length,
          limit: MEETING_HISTORY_PAGE_SIZE,
          offset: meetings.length,
        };
        const byId = new Set(meetings.map((m) => m.id));
        const appended = [...meetings];
        for (const item of page.items) {
          if (byId.has(item.id)) continue;
          byId.add(item.id);
          appended.push(item);
        }

        set({
          meetings: appended,
          total: page.total,
          hasMore: appended.length < page.total,
          loadingMore: false,
          error: null,
        });
      } catch (err) {
        if (
          requestGeneration === loadMoreRequestGeneration &&
          listGeneration === meetingListRequestGeneration
        ) {
          set({
            loadingMore: false,
            error: `履歴の追加取得に失敗しました: ${toUserError(err)}`,
          });
        }
      }
    },

    selectMeeting: async (id: string) => {
      const requestGeneration = ++meetingDetailRequestGeneration;
      set({ selectedMeetingId: id, loadingDetail: true, error: null });
      try {
        const res = await getMeetingMeetingsMeetingIdGet({
          path: { meeting_id: id },
        });
        if (requestGeneration !== meetingDetailRequestGeneration) return;

        if (res.error) {
          set({
            loadingDetail: false,
            error: `詳細の取得に失敗しました: ${formatApiError(res.error)}`,
          });
          return;
        }
        set({ selectedMeeting: res.data ?? null, loadingDetail: false });
      } catch (err) {
        if (requestGeneration === meetingDetailRequestGeneration) {
          set({
            loadingDetail: false,
            error: `詳細の取得に失敗しました: ${toUserError(err)}`,
          });
        }
      }
    },

    updateTitle: async (id: string, title: string) => {
      set({ saving: true, error: null });
      try {
        const res = await updateMeetingTitleMeetingsMeetingIdPatch({
          path: { meeting_id: id },
          body: { title },
        });
        if (res.error) {
          set({
            saving: false,
            error: `タイトルの更新に失敗しました: ${formatApiError(res.error)}`,
          });
          return;
        }

        const { selectedMeeting, meetings } = get();

        // Optimistically update in-memory state
        const updated: Partial<MeetingHistoryState> = { saving: false };
        if (selectedMeeting?.id === id) {
          updated.selectedMeeting = { ...selectedMeeting, title };
        }
        updated.meetings = meetings.map((m) =>
          m.id === id ? { ...m, title } : m,
        );
        set(updated);
      } catch (err) {
        set({
          saving: false,
          error: `タイトルの更新に失敗しました: ${toUserError(err)}`,
        });
      }
    },


    generateMinutes: async (id: string) => {
      const requestGeneration = ++minutesRequestGeneration;
      const previousController = minutesAbortController;
      minutesAbortController = null;
      previousController?.abort();

      const controller = new AbortController();
      minutesAbortController = controller;
      const isCurrentRequest = () =>
        requestGeneration === minutesRequestGeneration &&
        minutesAbortController === controller;

      set({
        minutesStatus: "generating",
        minutesProgress: "",
        minutesError: null,
        error: null,
      });
      try {
        const response = await streamMeetingMinutes(id, controller.signal);
        if (!isCurrentRequest()) return;
        if (!response.ok || !response.body) {
          throw new Error("minutes request failed");
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        while (true) {
          const { done, value } = await reader.read();
          if (!isCurrentRequest()) return;
          if (done) break;
          if (value) {
            set({
              minutesProgress:
                get().minutesProgress + decoder.decode(value, { stream: true }),
            });
          }
        }
        const tail = decoder.decode();
        if (!isCurrentRequest()) return;
        if (tail) set({ minutesProgress: get().minutesProgress + tail });

        // Do not navigate back to a meeting the user has since left.
        if (get().selectedMeetingId === id) {
          await get().selectMeeting(id);
        }
        if (isCurrentRequest()) {
          set({ minutesStatus: "idle", minutesError: null });
        }
      } catch (err) {
        if (!isCurrentRequest()) return;

        if (
          controller.signal.aborted ||
          (err instanceof DOMException && err.name === "AbortError")
        ) {
          set({ minutesStatus: "cancelled" });
        } else {
          set({
            minutesStatus: "error",
            minutesError: "要約・議事録を作成できませんでした。",
          });
        }
      } finally {
        if (isCurrentRequest()) minutesAbortController = null;
      }
    },

    cancelMinutes: () => {
      const controller = minutesAbortController;
      if (!controller) return;

      ++minutesRequestGeneration;
      minutesAbortController = null;
      controller.abort();
      set({ minutesStatus: "cancelled", minutesError: null });
    },

    deleteMeeting: async (id: string) => {
      set({ deleting: true, error: null });
      try {
        const res = await deleteMeetingMeetingsMeetingIdDelete({
          path: { meeting_id: id },
        });
        if (res.error) {
          set({
            deleting: false,
            error: `削除に失敗しました: ${formatApiError(res.error)}`,
          });
          return;
        }

        // Clear selection if the deleted item was selected, then refresh
        const wasSelected = get().selectedMeetingId === id;
        if (wasSelected) {
          ++meetingDetailRequestGeneration;
          set({
            selectedMeetingId: null,
            selectedMeeting: null,
            loadingDetail: false,
          });
        }

        // Clear deleting flag before reloading to avoid overlapping flags
        set({ deleting: false });
        await get().loadMeetings();
      } catch (err) {
        set({
          deleting: false,
          error: `削除に失敗しました: ${toUserError(err)}`,
        });
      }
    },

    reset: () => {
      ++meetingListRequestGeneration;
      ++loadMoreRequestGeneration;
      ++meetingDetailRequestGeneration;
      ++minutesRequestGeneration;
      minutesAbortController?.abort();
      minutesAbortController = null;
      set(INITIAL);
    },
  }),
);
