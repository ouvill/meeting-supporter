export type PreviewMode =
  | "assistant-panel"
  | "meeting-workspace"
  | "new-meeting"
  | "settings"
  | "history";

const ASSISTANT_PANEL_PREVIEW: PreviewMode = "assistant-panel";
const MEETING_WORKSPACE_PREVIEW: PreviewMode = "meeting-workspace";
const NEW_MEETING_PREVIEW: PreviewMode = "new-meeting";
const SETTINGS_PREVIEW: PreviewMode = "settings";
const HISTORY_PREVIEW: PreviewMode = "history";

export function getPreviewModeFromSearch(search: string): PreviewMode | null {
  const params = new URLSearchParams(search);
  const preview = params.get("preview");

  if (preview === ASSISTANT_PANEL_PREVIEW) return ASSISTANT_PANEL_PREVIEW;
  if (preview === MEETING_WORKSPACE_PREVIEW) return MEETING_WORKSPACE_PREVIEW;
  if (preview === NEW_MEETING_PREVIEW) return NEW_MEETING_PREVIEW;
  if (preview === SETTINGS_PREVIEW) return SETTINGS_PREVIEW;
  if (preview === HISTORY_PREVIEW) return HISTORY_PREVIEW;
  return null;
}

export function isAssistantPanelPreviewEnabled(
  search: string,
  isDev: boolean,
): boolean {
  return isDev && getPreviewModeFromSearch(search) === ASSISTANT_PANEL_PREVIEW;
}

export function isMeetingWorkspacePreviewEnabled(
  search: string,
  isDev: boolean,
): boolean {
  return (
    isDev && getPreviewModeFromSearch(search) === MEETING_WORKSPACE_PREVIEW
  );
}

export function isNewMeetingPreviewEnabled(
  search: string,
  isDev: boolean,
): boolean {
  return isDev && getPreviewModeFromSearch(search) === NEW_MEETING_PREVIEW;
}

export function isSettingsPreviewEnabled(
  search: string,
  isDev: boolean,
): boolean {
  return isDev && getPreviewModeFromSearch(search) === SETTINGS_PREVIEW;
}

export function isHistoryPreviewEnabled(
  search: string,
  isDev: boolean,
): boolean {
  return isDev && getPreviewModeFromSearch(search) === HISTORY_PREVIEW;
}
