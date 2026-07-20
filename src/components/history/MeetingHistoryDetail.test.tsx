import {
  act,
  render,
  screen,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MeetingHistoryDetail } from "./MeetingHistoryDetail";
import type { MeetingDetail } from "../../api/generated/types.gen";

// ── Fixture ───────────────────────────────────────────────────────

function makeMeeting(overrides?: Partial<MeetingDetail>): MeetingDetail {
  return {
    id: "mtg-001",
    title: "Test Meeting",
    status: "completed",
    started_at: "2026-06-01T10:00:00Z",
    duration_seconds: 3600,
    ended_at: "2026-06-01T11:00:00Z",
    turns: [],
    reply_suggestions: [],
    recording_assets: [],
    ai_note: "",
    ...overrides,
  };
}

function makeMeetingWithTranscript(
  overrides?: Partial<MeetingDetail>,
): MeetingDetail {
  return makeMeeting({
    turns: [
      {
        id: "turn-001",
        sequence: 1,
        speaker: "顧客",
        text: "次回までに見積もりを送付してください。",
      },
    ],
    ...overrides,
  });
}

function withPersistedMinutes(minutes: string): MeetingDetail {
  return {
    ...makeMeetingWithTranscript(),
    minutes,
  } as unknown as MeetingDetail;
}

const defaultProps = {
  meeting: makeMeeting(),
  loadingDetail: false,
  saving: false,
  deleting: false,
  minutesStatus: "idle" as const,
  minutesProgress: "",
  minutesError: null,
  minutesRouteStatus: {
    readiness: "ready" as const,
    canGenerate: true,
    message: null,
  },
  onGenerateMinutes: vi.fn().mockResolvedValue(undefined),
  onCancelMinutes: vi.fn(),
  onUpdateTitle: vi.fn().mockResolvedValue(undefined),
  onDelete: vi.fn().mockResolvedValue(undefined),
};

// ── Tests ──────────────────────────────────────────────────────────

describe("MeetingHistoryDetail", () => {
  // ── Loading state ──────────────────────────────────────────────

  it("renders loading skeleton when loadingDetail is true", () => {
    const { container } = render(
      <MeetingHistoryDetail {...defaultProps} loadingDetail={true} />,
    );
    expect(container.querySelectorAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("renders meeting content when loaded", () => {
    render(<MeetingHistoryDetail {...defaultProps} />);
    expect(screen.getByText("Test Meeting")).toBeInTheDocument();
  });

  it("hides AI memo section even when ai_note is present", () => {
    render(
      <MeetingHistoryDetail
        {...defaultProps}
        meeting={makeMeeting({
          ai_note: "## 顧客情報\n\n- 予算は来月確定",
        })}
      />,
    );

    expect(screen.queryByText("AI メモ")).not.toBeInTheDocument();
    expect(screen.queryByText("顧客情報")).not.toBeInTheDocument();
    expect(screen.queryByText("予算は来月確定")).not.toBeInTheDocument();
  });

  it("does not offer minutes generation until a completed meeting has a persisted transcript", () => {
    const { rerender } = render(<MeetingHistoryDetail {...defaultProps} />);

    expect(
      screen.queryByRole("button", { name: "議事録を生成" }),
    ).not.toBeInTheDocument();
    expect(defaultProps.onGenerateMinutes).not.toHaveBeenCalled();

    rerender(
      <MeetingHistoryDetail
        {...defaultProps}
        meeting={makeMeetingWithTranscript({ status: "recording" })}
      />,
    );
    expect(
      screen.queryByRole("button", { name: "議事録を生成" }),
    ).not.toBeInTheDocument();

    rerender(
      <MeetingHistoryDetail
        {...defaultProps}
        meeting={makeMeetingWithTranscript()}
      />,
    );
    expect(screen.getByRole("button", { name: "議事録を生成" })).toBeEnabled();
    expect(defaultProps.onGenerateMinutes).not.toHaveBeenCalled();
  });

  it("keeps generation unavailable while preserving minutes and offering settings", () => {
    const routeError =
      "議事録AIの準備ができていません。設定を確認してから再試行してください。";
    const onSettings = vi.fn();
    render(
      <MeetingHistoryDetail
        {...defaultProps}
        meeting={withPersistedMinutes("# 決定事項\n\n- 保存済みの議事録")}
        minutesRouteStatus={{
          readiness: "setup_required",
          canGenerate: false,
          message: routeError,
        }}
        onSettings={onSettings}
      />,
    );

    expect(screen.getByRole("button", { name: "議事録を生成" })).toBeDisabled();
    expect(
      screen.getByText("AIの準備を確認してから作成できます。"),
    ).toBeInTheDocument();
    expect(screen.getByText(routeError)).toBeInTheDocument();
    expect(screen.getByText("保存済みの議事録")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "設定を確認" }));
    expect(onSettings).toHaveBeenCalledOnce();
  });

  it("shows streamed progress and lets the user cancel the active generation", () => {
    const onCancelMinutes = vi.fn();
    render(
      <MeetingHistoryDetail
        {...defaultProps}
        meeting={makeMeetingWithTranscript()}
        minutesStatus="generating"
        minutesProgress={"# 議事録\n\n- 見積もりを送付"}
        onCancelMinutes={onCancelMinutes}
        minutesRouteStatus={{
          readiness: "unavailable",
          canGenerate: false,
          message: "現在は利用できません。",
        }}
      />,
    );

    expect(screen.getByText("議事録")).toBeInTheDocument();
    expect(screen.getByText("見積もりを送付")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "生成を中止" }));
    expect(onCancelMinutes).toHaveBeenCalledOnce();
  });

  it("renders refreshed persisted minutes and permits recovery or deliberate re-generation", () => {
    const onGenerateMinutes = vi.fn().mockResolvedValue(undefined);
    const recoverableError =
      "議事録の生成に失敗しました。もう一度お試しください。";
    render(
      <MeetingHistoryDetail
        {...defaultProps}
        meeting={withPersistedMinutes("# 決定事項\n\n- 見積もりを送付")}
        minutesStatus="error"
        minutesError={recoverableError}
        onGenerateMinutes={onGenerateMinutes}
      />,
    );

    expect(screen.getByText("決定事項")).toBeInTheDocument();
    expect(screen.getByText("見積もりを送付")).toBeInTheDocument();
    expect(screen.getByText(recoverableError)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "議事録を生成" }));
    expect(onGenerateMinutes).toHaveBeenCalledWith("mtg-001");
  });

  it("shows stored suggestion labels as reply styles without exposing agent ids", () => {
    render(
      <MeetingHistoryDetail
        {...defaultProps}
        meeting={makeMeeting({
          reply_suggestions: [
            {
              id: "suggestion-1",
              target_turn_id: "turn-123456789",
              sequence: 1,
              agent_id: "reply_polite",
              agent_label: "丁寧",
              text: "恐れ入りますが、来週水曜で調整できます。",
            },
          ],
        })}
      />,
    );

    expect(screen.getByText("スタイル: 丁寧")).toBeInTheDocument();
    expect(
      screen.getByText("恐れ入りますが、来週水曜で調整できます。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("reply_polite")).not.toBeInTheDocument();
  });

  // ── Delete dialog ──────────────────────────────────────────────

  it("opens delete dialog with correct ARIA attributes when delete button is clicked", () => {
    render(<MeetingHistoryDetail {...defaultProps} />);

    const deleteBtn = screen.getByLabelText("削除");
    fireEvent.click(deleteBtn);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-labelledby", "delete-dialog-title");
    expect(dialog).toHaveAttribute("aria-describedby", "delete-dialog-desc");
  });

  it("displays dialog title and description text", () => {
    render(<MeetingHistoryDetail {...defaultProps} />);
    fireEvent.click(screen.getByLabelText("削除"));

    expect(screen.getByText("会議を削除しますか？")).toBeInTheDocument();
    expect(
      screen.getByText(
        "この操作は元に戻せません。データベースと録音ファイルが完全に削除されます。",
      ),
    ).toBeInTheDocument();
  });

  it("closes dialog when Escape is pressed", () => {
    render(<MeetingHistoryDetail {...defaultProps} />);
    fireEvent.click(screen.getByLabelText("削除"));

    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes dialog when cancel button is clicked", () => {
    render(<MeetingHistoryDetail {...defaultProps} />);
    fireEvent.click(screen.getByLabelText("削除"));

    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.click(screen.getByText("キャンセル"));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("calls onDelete when confirm button is clicked", () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    render(<MeetingHistoryDetail {...defaultProps} onDelete={onDelete} />);

    fireEvent.click(screen.getByLabelText("削除"));
    fireEvent.click(screen.getByText("削除する"));

    expect(onDelete).toHaveBeenCalledWith("mtg-001");
  });

  it("focuses the cancel button when dialog opens", async () => {
    render(<MeetingHistoryDetail {...defaultProps} />);
    fireEvent.click(screen.getByLabelText("削除"));

    await waitFor(() => {
      const cancelBtn = screen.getByText("キャンセル");
      expect(cancelBtn).toHaveFocus();
    });
  });

  it("disables action buttons while deleting", () => {
    render(<MeetingHistoryDetail {...defaultProps} deleting={true} />);
    fireEvent.click(screen.getByLabelText("削除"));

    expect(screen.getByText("キャンセル")).toBeDisabled();
    expect(screen.getByText("削除する")).toBeDisabled();
  });

  // ── Tab trap within dialog ─────────────────────────────────────

  it("traps Tab focus within dialog (Tab from last element wraps to first)", () => {
    render(<MeetingHistoryDetail {...defaultProps} />);
    fireEvent.click(screen.getByLabelText("削除"));

    const cancelBtn = screen.getByText("キャンセル");
    const confirmBtn = screen.getByText("削除する");
    const dialog = screen.getByRole("dialog");

    // Focus the last element (confirm button)
    confirmBtn.focus();
    expect(document.activeElement).toBe(confirmBtn);

    // Tab on last element → should wrap to first (cancel button)
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: false });
    expect(document.activeElement).toBe(cancelBtn);
  });

  it("traps Shift+Tab focus within dialog (Shift+Tab from first wraps to last)", () => {
    render(<MeetingHistoryDetail {...defaultProps} />);
    fireEvent.click(screen.getByLabelText("削除"));

    const cancelBtn = screen.getByText("キャンセル");
    const confirmBtn = screen.getByText("削除する");
    const dialog = screen.getByRole("dialog");

    // Focus the first element (cancel button)
    cancelBtn.focus();

    // Shift+Tab on first element → should wrap to last element
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(confirmBtn);
  });

  // ── Focus restore on cancel ────────────────────────────────────

  it("restores focus to delete button when dialog is cancelled", async () => {
    render(<MeetingHistoryDetail {...defaultProps} />);

    const deleteBtn = screen.getByLabelText("削除");
    fireEvent.click(deleteBtn);

    // Dialog is open
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    // Click cancel — uses requestAnimationFrame to restore, so we wait
    fireEvent.click(screen.getByText("キャンセル"));

    await waitFor(() => {
      expect(deleteBtn).toHaveFocus();
    });
  });

  it("restores focus to delete button when dialog is closed via Escape", async () => {
    render(<MeetingHistoryDetail {...defaultProps} />);

    const deleteBtn = screen.getByLabelText("削除");
    fireEvent.click(deleteBtn);

    expect(screen.getByRole("dialog")).toBeInTheDocument();

    const dialog = screen.getByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });

    await waitFor(() => {
      expect(deleteBtn).toHaveFocus();
    });
  });

  // ── Inline title editor focus restoration ──────────────────────────

  it("restores focus to title heading after Enter save in inline editor", async () => {
    render(<MeetingHistoryDetail {...defaultProps} />);

    // Click the title to start editing
    const title = screen.getByText("Test Meeting");
    fireEvent.click(title);

    // Input should appear
    const input = screen.getByRole("textbox");
    expect(input).toBeInTheDocument();

    // Focus the input explicitly (autoFocus may not work in jsdom)
    act(() => {
      input.focus();
    });

    // Press Enter to save — focus should return to the title heading
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      expect(screen.getByText("Test Meeting")).toHaveFocus();
    });
  });

  it("restores focus to title heading after Escape cancel in inline editor", async () => {
    render(<MeetingHistoryDetail {...defaultProps} />);

    // Click the title to start editing
    const title = screen.getByText("Test Meeting");
    fireEvent.click(title);

    // Input should appear
    const input = screen.getByRole("textbox");

    // Focus the input explicitly
    act(() => {
      input.focus();
    });

    // Press Escape to cancel — focus should return to the title heading
    fireEvent.keyDown(input, { key: "Escape" });

    await waitFor(() => {
      expect(screen.getByText("Test Meeting")).toHaveFocus();
    });
  });
});
