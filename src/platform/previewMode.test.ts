import { describe, expect, it } from "vitest";
import {
  getPreviewModeFromSearch,
  isAssistantPanelPreviewEnabled,
  isMeetingWorkspacePreviewEnabled,
} from "./previewMode";

describe("previewMode", () => {
  it("assistant panel preview query を判定する", () => {
    expect(getPreviewModeFromSearch("?preview=assistant-panel")).toBe(
      "assistant-panel",
    );
  });

  it("meeting workspace preview query を判定する", () => {
    expect(getPreviewModeFromSearch("?preview=meeting-workspace")).toBe(
      "meeting-workspace",
    );
    expect(
      isMeetingWorkspacePreviewEnabled("?preview=meeting-workspace", true),
    ).toBe(true);
    expect(
      isMeetingWorkspacePreviewEnabled("?preview=meeting-workspace", false),
    ).toBe(false);
  });

  it("未知の preview query は null を返す", () => {
    expect(getPreviewModeFromSearch("?preview=unknown")).toBeNull();
    expect(getPreviewModeFromSearch("")).toBeNull();
  });

  it("dev のときだけ assistant panel preview を有効化する", () => {
    expect(
      isAssistantPanelPreviewEnabled("?preview=assistant-panel", true),
    ).toBe(true);
    expect(
      isAssistantPanelPreviewEnabled("?preview=assistant-panel", false),
    ).toBe(false);
  });
});
