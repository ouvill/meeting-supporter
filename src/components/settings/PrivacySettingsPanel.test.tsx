import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PrivacySettingsPanel } from "./PrivacySettingsPanel";
import { INITIAL_SETTINGS_FORM } from "./settingsFormMapping";

const api = vi.hoisted(() => ({ preview: vi.fn(), execute: vi.fn() }));
vi.mock("../../api/recordingRetention", () => ({
  previewRecordingCleanup: api.preview,
  executeRecordingCleanup: api.execute,
}));
const preview = {
  candidate_meeting_ids: ["synthetic-meeting"],
  delete_count: 1,
  delete_recording_bytes: 100,
  total_recording_bytes_before: 100,
  total_recording_bytes_after: 0,
};

function setup() {
  render(
    <PrivacySettingsPanel
      form={{
        ...INITIAL_SETTINGS_FORM,
        recordingCleanupCutoffDate: "2025-01-02",
      }}
      selectedRoute={null}
      errors={{}}
      update={vi.fn()}
      onChooseContextDirectory={vi.fn()}
    />,
  );
}

async function confirm() {
  fireEvent.click(screen.getByRole("button", { name: "削除対象を確認" }));
  fireEvent.click(await screen.findByRole("button", { name: "1件を削除する" }));
  expect(api.execute).not.toHaveBeenCalled();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "削除を実行する" }));
  });
}

describe("recording cleanup confirmation", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.preview.mockResolvedValue(preview);
  });

  it("executes only after preview and explicit confirmation", async () => {
    api.execute.mockResolvedValue({
      ...preview,
      deleted_meeting_ids: ["synthetic-meeting"],
      failed_meeting_ids: [],
      skipped_meeting_ids: [],
    });
    setup();
    expect(api.execute).not.toHaveBeenCalled();
    await confirm();
    expect(await screen.findByText("1件を削除しました。")).toBeInTheDocument();
    expect(api.execute).toHaveBeenCalledTimes(1);
    expect(api.execute).toHaveBeenCalledWith({
      cutoff_date: "2025-01-02",
      max_total_bytes: null,
    });
  });

  it("requires another preview when execution cannot be confirmed", async () => {
    api.execute.mockRejectedValue(new Error("synthetic stale preview"));
    setup();
    await confirm();
    expect(
      await screen.findByText(
        "削除結果を確認できませんでした。削除対象を再確認してください。",
      ),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "1件を削除する" }),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: "削除を実行する" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "削除対象を確認" }));
    expect(
      await screen.findByRole("button", { name: "1件を削除する" }),
    ).toBeInTheDocument();
    expect(api.preview).toHaveBeenCalledTimes(2);
    expect(api.execute).toHaveBeenCalledTimes(1);
  });
});
