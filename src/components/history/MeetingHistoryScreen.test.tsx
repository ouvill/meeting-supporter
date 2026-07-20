import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingHistoryScreen } from "./MeetingHistoryScreen";
import { useMeetingHistoryStore } from "../../store/meetingHistoryStore";
import type {
  MeetingDetail,
  MeetingListItem,
} from "../../api/generated/types.gen";
import type { AiUseCaseRouteStatus } from "../../hooks/useAiRoutes";

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

const READY_MINUTES_ROUTE: AiUseCaseRouteStatus = {
  readiness: "ready",
  canGenerate: true,
  message: null,
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
    render(
      <MeetingHistoryScreen
        onBack={() => {}}
        minutesRouteStatus={READY_MINUTES_ROUTE}
        onSettings={() => {}}
      />,
    );

    const selectedMeeting = screen.getByRole("button", {
      name: /顧客との打ち合わせ/,
    });
    await act(async () => {
      fireEvent.click(selectedMeeting);
      fireEvent.click(screen.getByRole("button", { name: "会議一覧" }));
    });

    expect(selectedMeeting).toHaveFocus();
  });

  it("guards minutes generation with shared readiness and opens settings", async () => {
    const generateMinutes = vi.fn().mockResolvedValue(undefined);
    const onSettings = vi.fn();
    useMeetingHistoryStore.setState({
      selectedMeeting: {
        ...detail,
        turns: [
          {
            id: "turn-1",
            sequence: 1,
            speaker: "other",
            text: "議事録に残す発言",
          },
        ],
      },
      generateMinutes,
    });

    render(
      <MeetingHistoryScreen
        onBack={() => {}}
        minutesRouteStatus={{
          readiness: "setup_required",
          canGenerate: false,
          message: "議事録を利用する支援方法を設定してください。",
        }}
        onSettings={onSettings}
      />,
    );

    const generate = screen.getByRole("button", { name: "議事録を生成" });
    expect(generate).toBeDisabled();
    fireEvent.click(generate);
    expect(generateMinutes).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "設定を確認" }));
    expect(onSettings).toHaveBeenCalledOnce();
  });
});
