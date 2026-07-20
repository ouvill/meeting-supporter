import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MeetingHistoryList } from "./MeetingHistoryList";
import type { MeetingListItem } from "../../api/generated/types.gen";

// ── Fixture helpers ────────────────────────────────────────────────

function makeMeeting(
  overrides: Partial<MeetingListItem> & { id: string },
): MeetingListItem {
  return {
    title: null,
    started_at: "2026-06-01T10:00:00Z",
    status: "completed",
    duration_seconds: null,
    ended_at: null,
    has_ai_note: false,
    has_recording: false,
    ...overrides,
  };
}

const baseProps = {
  meetings: [] as MeetingListItem[],
  selectedId: null as string | null,
  onSelect: () => {},
  loading: false,
  error: null as string | null,
  hasMore: false,
  loadingMore: false,
  onLoadMore: () => {},
};

// ── Suite ──────────────────────────────────────────────────────────

describe("MeetingHistoryList", () => {
  // ── Loading state ──────────────────────────────────────────────

  it("renders loading skeleton when loading is true", () => {
    const { container } = render(
      <MeetingHistoryList {...baseProps} loading={true} />,
    );
    // Should have skeleton divs
    expect(container.querySelectorAll(".skeleton").length).toBeGreaterThan(0);
  });

  // ── Error state ────────────────────────────────────────────────

  it("renders error message when error is set", () => {
    render(<MeetingHistoryList {...baseProps} error="Network error" />);
    expect(screen.getByText("Network error")).toBeInTheDocument();
  });

  // ── Empty state ────────────────────────────────────────────────

  it("renders empty message when no meetings", () => {
    render(<MeetingHistoryList {...baseProps} meetings={[]} />);
    expect(screen.getByText("会議履歴がありません")).toBeInTheDocument();
  });

  // ── hidden has_ai_note indicator ───────────────────────────────

  it("does not render AI memo indicator even when has_ai_note is true", () => {
    const meetings = [makeMeeting({ id: "m1", has_ai_note: true })];
    render(<MeetingHistoryList {...baseProps} meetings={meetings} />);
    expect(screen.queryByLabelText("AI メモあり")).not.toBeInTheDocument();
  });

  it("does not render AI memo indicator when has_ai_note is false", () => {
    const meetings = [makeMeeting({ id: "m1", has_ai_note: false })];
    render(<MeetingHistoryList {...baseProps} meetings={meetings} />);
    expect(screen.queryByLabelText("AI メモあり")).not.toBeInTheDocument();
  });

  // ── has_recording indicator ───────────────────────────────────

  it("renders recording indicator with accessible label when has_recording is true", () => {
    const meetings = [makeMeeting({ id: "m1", has_recording: true })];
    render(<MeetingHistoryList {...baseProps} meetings={meetings} />);
    expect(screen.getByLabelText("録音ファイルあり")).toBeInTheDocument();
  });

  it("does not render recording indicator when has_recording is false", () => {
    const meetings = [makeMeeting({ id: "m1", has_recording: false })];
    render(<MeetingHistoryList {...baseProps} meetings={meetings} />);
    expect(screen.queryByLabelText("録音ファイルあり")).not.toBeInTheDocument();
  });

  // ── Recording indicator with hidden AI memo indicator ──────────

  it("renders recording indicator without AI memo indicator when both flags are true", () => {
    const meetings = [
      makeMeeting({ id: "m1", has_ai_note: true, has_recording: true }),
    ];
    render(<MeetingHistoryList {...baseProps} meetings={meetings} />);
    expect(screen.queryByLabelText("AI メモあり")).not.toBeInTheDocument();
    expect(screen.getByLabelText("録音ファイルあり")).toBeInTheDocument();
  });

  // ── Neither indicator when both flags are false ───────────────

  it("renders neither indicator when both flags are false", () => {
    const meetings = [
      makeMeeting({ id: "m1", has_ai_note: false, has_recording: false }),
    ];
    render(<MeetingHistoryList {...baseProps} meetings={meetings} />);
    expect(screen.queryByLabelText("AI メモあり")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("録音ファイルあり")).not.toBeInTheDocument();
  });

  // ── data-meeting-id attribute ─────────────────────────────────

  it("sets data-meeting-id on each item button matching the meeting id", () => {
    const meetingData = [
      { id: "m1", title: "Alpha" },
      { id: "m2", title: "Beta" },
      { id: "m3", title: "Gamma" },
    ] as const;
    const meetings = meetingData.map((d) =>
      makeMeeting({ id: d.id, title: d.title }),
    );
    render(<MeetingHistoryList {...baseProps} meetings={meetings} />);

    meetingData.forEach(({ id, title }) => {
      const btn = screen.getByText(title).closest("button");
      expect(btn).toHaveAttribute("data-meeting-id", id);
    });
  });

  // ── aria-current on selected item ──────────────────────────────

  it('adds aria-current="page" to the selected item', () => {
    const meetings = [
      makeMeeting({ id: "m1", title: "First meeting" }),
      makeMeeting({ id: "m2", title: "Second meeting" }),
    ];
    render(
      <MeetingHistoryList {...baseProps} meetings={meetings} selectedId="m2" />,
    );

    // Selected item should have aria-current
    const selectedBtn = screen.getByText("Second meeting").closest("button");
    expect(selectedBtn).toHaveAttribute("aria-current", "page");

    // Non-selected item should NOT have aria-current
    const nonSelectedBtn = screen.getByText("First meeting").closest("button");
    expect(nonSelectedBtn).not.toHaveAttribute("aria-current");
  });

  it("renders load more button when more pages are available", () => {
    const meetings = [makeMeeting({ id: "m1", title: "First meeting" })];
    render(
      <MeetingHistoryList {...baseProps} meetings={meetings} hasMore={true} />,
    );

    expect(
      screen.getByRole("button", { name: "さらに表示" }),
    ).toBeInTheDocument();
  });

  it("disables load more button while loading more items", () => {
    const meetings = [makeMeeting({ id: "m1", title: "First meeting" })];
    render(
      <MeetingHistoryList
        {...baseProps}
        meetings={meetings}
        hasMore={true}
        loadingMore={true}
      />,
    );

    expect(
      screen.getByRole("button", { name: "読み込み中..." }),
    ).toBeDisabled();
  });
});
