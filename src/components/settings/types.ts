export type SettingsCategory =
  | "support"
  | "account"
  | "audio"
  | "privacy"
  | "advanced"
  | "about";

export type ConnectionUiState =
  | "unconfigured"
  | "draft-unverified"
  | "saved-unverified"
  | "verified"
  | "failed"
  | "pending-delete";

export function isConnectionUsable(state: ConnectionUiState): boolean {
  return (
    state === "draft-unverified" ||
    state === "saved-unverified" ||
    state === "verified"
  );
}

export interface ReplyStyleFormItem {
  id: string;
  label: string;
  enabled: boolean;
  priority: number;
}

export interface SettingsForm {
  secretsStatus: Record<string, boolean>;
  secretInputs: Record<string, string>;
  ollamaBaseUrl: string;
  acpCommand: string;
  sttBackend: string;
  sttWhisperModel: string;
  sttDeepgramModel: string;
  sttOpenaiModel: string;
  sttVoskModelPath: string;
  sttLang: string;
  sttVad: number;
  sttSilence: number;
  replyFeatureEnabled: boolean;
  replyAutoGenerate: boolean;
  replyStyles: ReplyStyleFormItem[];
  infoFeatureEnabled: boolean;
  usageMeetingLimitJpy: number;
  usageMonthlyLimitJpy: number;
  dataDir: string;
  contextDir: string;
  recordingCleanupCutoffDate: string;
  recordingCleanupMaxMegabytes: number;
}

export interface SettingsFieldErrors {
  support?: string;
  audio?: string;
  advanced?: string;
  contextDir?: string;
}
