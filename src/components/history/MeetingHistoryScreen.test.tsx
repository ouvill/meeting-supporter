import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingHistoryScreen } from "./MeetingHistoryScreen";
import { useMeetingHistoryStore } from "../../store/meetingHistoryStore";
import type {
  MeetingDetail,
  MeetingListItem,
} from "../../api/generated/types.gen";

const meeting: MeetingListItem = {
  id: "meeting-1",
  title: "顧客との打ち合わせ",
  status: "completed",
  started_at: "2026-07-01T09:00:00Z",
  duration_seconds: 1800,
  ended_at: "2026-07-01T09:30:00Z",
  has_ai_note: false,
  has_recording: false,
};

const detail: MeetingDetail = {
  ...meeting,
  turns: [],
  reply_suggestions: [],
  recording_assets: [],
  ai_note: "",
};

const resetHistoryStore = useMeetingHistoryStore.getState().reset;

beforeEach(() => {
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 0;
  });
  useMeetingHistoryStore.setState({
    meetings: [meeting],
    total: 1,
    hasMore: false,
    selectedMeetingId: "meeting-1",
    selectedMeeting: detail,
    loading: false,
    loadingDetail: false,
    loadingMore: false,
    error: null,
    saving: false,
    deleting: false,
    loadMeetings: async () => {},
    loadMore: async () => {},
    selectMeeting: async () => {},
    updateTitle: async () => {},
    deleteMeeting: async () => {},
  });
});

afterEach(() => {
  act(() => {
    resetHistoryStore();
  });
  vi.unstubAllGlobals();
});

describe("MeetingHistoryScreen", () => {
  it("returns focus to the selected meeting after the compact detail back action", async () => {
    render(<MeetingHistoryScreen onBack={() => {}} />);

    const selectedMeeting = screen.getByRole("button", {
      name: /顧客との打ち合わせ/,
    });
    await act(async () => {
      fireEvent.click(selectedMeeting);
      fireEvent.click(screen.getByRole("button", { name: "会議一覧" }));
    });

    expect(selectedMeeting).toHaveFocus();
  });
});
