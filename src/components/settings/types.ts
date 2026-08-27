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

export type VadEngine = "silero" | "webrtc";

export function isVadEngine(value: unknown): value is VadEngine {
  return value === "silero" || value === "webrtc";
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
  sttVadEngine: VadEngine;
  sttVadSensitivity: number;
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

export const STT_FORM_FIELDS = [
  "sttBackend",
  "sttWhisperModel",
  "sttDeepgramModel",
  "sttOpenaiModel",
  "sttVoskModelPath",
  "sttLang",
  "sttVadEngine",
  "sttVadSensitivity",
  "sttVad",
  "sttSilence",
] as const satisfies readonly (keyof SettingsForm)[];


export interface SettingsFieldErrors {
  support?: string;
  audio?: string;
  advanced?: string;
  contextDir?: string;
}
