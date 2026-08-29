import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LiveReplySidePanelPreview } from "./LiveReplySidePanelPreview";

vi.mock("../../hooks/useAlwaysOnTop", () => ({
  ASSISTANT_ALWAYS_ON_TOP_KEY: "meeting-supporter.assistant-always-on-top",
  useAlwaysOnTop: () => ({
    actual: "on",
    busy: false,
    issue: null,
    statusMessage: null,
    toggle: vi.fn(async () => {}),
    retry: vi.fn(async () => {}),
  }),
}));

vi.mock("../../hooks/useAiRoutes", () => ({
  useAiRoutes: () => ({
    routes: [],
    assignments: { reply: "preview", info: null, minutes: null },
    assignedRoutes: {
      reply: {
        id: "preview",
        readiness: "ready",
        selectable: true,
      },
      info: null,
      minutes: null,
    },
    infoRouteStatus: {
      readiness: "setup_required",
      canGenerate: false,
      message: "会話メモを利用する支援方法を設定してください。",
    },
    minutesRouteStatus: {
      readiness: "setup_required",
      canGenerate: false,
      message: "議事録を利用する支援方法を設定してください。",
    },
    replyStatus: { readiness: "ready", canGenerate: true, message: null },
    draftAssignments: { reply: "preview", info: null, minutes: null },
    assignmentDirty: false,
    setDraftAssignment: vi.fn(),
    resetDraftAssignments: vi.fn(),
    loading: false,
    saving: false,
    error: null,
    manualReloadStatus: "idle",
    reload: vi.fn(),
    saveAssignments: vi.fn(),
  }),
}));

describe("LiveReplySidePanelPreview", () => {
  it("shows the generated scenario and reply by default", () => {
    render(<LiveReplySidePanelPreview />);

    expect(screen.getByRole("button", { name: /^生成後/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /^待機中/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(
      screen.getByText(
        "承知しました。料金改定の背景は、提供価値の拡大とサポート体制強化の2点に絞って、1枚で説明できる形にまとめます。",
      ),
    ).toBeInTheDocument();
  });

  it("switches to the long-history latest utterance and reply", () => {
    render(<LiveReplySidePanelPreview />);

    fireEvent.click(screen.getByRole("button", { name: /^長い履歴/ }));

    expect(screen.getByRole("button", { name: /^長い履歴/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByText("対応 18: 影響範囲を整理して、次回までに確認します。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "まず影響範囲を整理し、適用時期と顧客告知のタイミングを分けて確認させてください。",
      ),
    ).toBeInTheDocument();
  });

  it("shows the actionable Codex failure in the failed scenario", () => {
    render(<LiveReplySidePanelPreview />);

    fireEvent.click(screen.getByRole("button", { name: /^生成失敗/ }));

    expect(
      screen.getByText(
        "Codex との通信が途中で切れました。接続を確認してもう一度お試しください。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "再試行" })).toBeEnabled();
  });

  it("shows an idle status and disables reply generation while waiting", () => {
    render(<LiveReplySidePanelPreview />);

    fireEvent.click(screen.getByRole("button", { name: /^待機中/ }));

    expect(screen.getByRole("status")).toHaveTextContent("待機中");
    expect(screen.getByText("発言を待っています")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返答案を作る" })).toBeDisabled();
  });
});
