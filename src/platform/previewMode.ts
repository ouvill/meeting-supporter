export type PreviewMode =
  | "assistant-panel"
  | "meeting-workspace"
  | "conversation-support";

const ASSISTANT_PANEL_PREVIEW: PreviewMode = "assistant-panel";
const MEETING_WORKSPACE_PREVIEW: PreviewMode = "meeting-workspace";
const CONVERSATION_SUPPORT_PREVIEW: PreviewMode = "conversation-support";

export function getPreviewModeFromSearch(search: string): PreviewMode | null {
  const params = new URLSearchParams(search);
  const preview = params.get("preview");

  if (preview === ASSISTANT_PANEL_PREVIEW) return ASSISTANT_PANEL_PREVIEW;
  if (preview === MEETING_WORKSPACE_PREVIEW) return MEETING_WORKSPACE_PREVIEW;
  if (preview === CONVERSATION_SUPPORT_PREVIEW)
    return CONVERSATION_SUPPORT_PREVIEW;
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

export function isConversationSupportPreviewEnabled(
  search: string,
  isDev: boolean,
): boolean {
  return (
    isDev && getPreviewModeFromSearch(search) === CONVERSATION_SUPPORT_PREVIEW
  );
}
