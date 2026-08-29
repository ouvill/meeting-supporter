import type {
  AgentSettingsPayload,
  ReplyStyleEnabledPatch,
  SecretsPayload,
  SettingsResponse,
  SettingsSaveRequest,
  TomlTable,
} from "../../api/generated/types.gen";
import type { ConnectionSecretKey } from "./ApiConnectionControl";
import {
  isVadEngine,
  STT_FORM_FIELDS,
  type ReplyStyleFormItem,
  type SettingsForm,
} from "./types";

export type SettingsResponseWithRetention = SettingsResponse & {
  recording_retention?: {
    cutoff_date?: string | null;
    max_total_bytes?: number | null;
  };
};

export type SettingsSaveRequestWithRetention = SettingsSaveRequest & {
  delete_secrets?: ConnectionSecretKey[];
  recording_retention: {
    cutoff_date: string | null;
    max_total_bytes: number | null;
  };
};

const DEFAULT_REPLY_STYLES: ReplyStyleFormItem[] = [
  { id: "standard", label: "標準", enabled: true, priority: 10 },
];

const SECRET_KEYS = [
  "GEMINI_API_KEY",
  "OPENAI_API_KEY",
  "ANTHROPIC_API_KEY",
  "DEEPGRAM_API_KEY",
  "XAI_API_KEY",
] as const;

export const INITIAL_SETTINGS_FORM: SettingsForm = {
  secretsStatus: {},
  secretInputs: {},
  ollamaBaseUrl: "http://localhost:11434/v1",
  acpCommand: "",
  sttBackend: "reazonspeech",
  sttWhisperModel: "large-v3-turbo",
  sttDeepgramModel: "nova-2",
  sttOpenaiModel: "gpt-4o-transcribe",
  sttVoskModelPath: "vosk-model-small-ja-0.22",
  sttLang: "ja",
  sttVadEngine: "silero",
  sttVadSensitivity: 0.4,
  sttVad: 2,
  sttSilence: 0.8,
  replyFeatureEnabled: true,
  replyAutoGenerate: false,
  replyStyles: DEFAULT_REPLY_STYLES,
  infoFeatureEnabled: true,
  usageMeetingLimitJpy: 0,
  usageMonthlyLimitJpy: 0,
  dataDir: "",
  contextDir: "",
  recordingCleanupCutoffDate: "",
  recordingCleanupMaxMegabytes: 0,
};

export function getTomlString(
  table: TomlTable | undefined,
  key: string,
): string | undefined {
  const value = table?.[key];
  return typeof value === "string" ? value : undefined;
}

function getTomlNumber(
  table: TomlTable | undefined,
  key: string,
): number | undefined {
  const value = table?.[key];
  return typeof value === "number" ? value : undefined;
}

export function mapSettingsResponseToForm(
  settings: SettingsResponseWithRetention,
): SettingsForm {
  const secretsStatus = settings.secrets as typeof settings.secrets & {
    XAI_API_KEY?: boolean;
  };
  const replyStyles = (
    settings.reply?.styles?.length
      ? settings.reply.styles
      : DEFAULT_REPLY_STYLES
  )
    .map((style) => ({
      id: style.id,
      label: style.label,
      enabled: style.enabled,
      priority: style.priority,
    }))
    .sort(
      (left, right) =>
        left.priority - right.priority || left.label.localeCompare(right.label),
    );
  const sttVadEngine = getTomlString(settings.stt, "vad_engine");
  const sttBackend = getTomlString(settings.stt, "backend") ?? "reazonspeech";
  const sttLanguage = getTomlString(settings.stt, "language") ?? "ja";

  return {
    secretsStatus: Object.fromEntries(
      SECRET_KEYS.map((key) => [key, secretsStatus[key] ?? false]),
    ),
    secretInputs: {},
    ollamaBaseUrl: settings.ollama?.base_url ?? "http://localhost:11434/v1",
    acpCommand: settings.acp?.command.join("\n") ?? "",
    sttBackend,
    sttWhisperModel:
      getTomlString(settings.stt, "whisper_model") ?? "large-v3-turbo",
    sttDeepgramModel: getTomlString(settings.stt, "deepgram_model") ?? "nova-2",
    sttOpenaiModel:
      getTomlString(settings.stt, "openai_model") ?? "gpt-4o-transcribe",
    sttVoskModelPath:
      getTomlString(settings.stt, "vosk_model_path") ??
      "vosk-model-small-ja-0.22",
    sttLang: sttBackend === "reazonspeech" ? "ja" : sttLanguage,
    sttVadEngine: isVadEngine(sttVadEngine) ? sttVadEngine : "silero",
    sttVadSensitivity: getTomlNumber(settings.stt, "vad_sensitivity") ?? 0.4,
    sttVad: getTomlNumber(settings.stt, "vad_aggressiveness") ?? 2,
    sttSilence: getTomlNumber(settings.stt, "silence_duration") ?? 0.8,
    replyFeatureEnabled: settings.reply?.enabled ?? true,
    replyAutoGenerate: settings.reply?.auto_generate ?? false,
    replyStyles,
    infoFeatureEnabled: settings.agents.info_enabled ?? true,
    usageMeetingLimitJpy: settings.usage?.budget?.meeting_limit_jpy ?? 0,
    usageMonthlyLimitJpy: settings.usage?.budget?.monthly_limit_jpy ?? 0,
    dataDir: settings.data_dir ?? "",
    contextDir: settings.context_dir ?? "",
    recordingCleanupCutoffDate: settings.recording_retention?.cutoff_date ?? "",
    recordingCleanupMaxMegabytes: settings.recording_retention?.max_total_bytes
      ? settings.recording_retention.max_total_bytes / (1024 * 1024)
      : 0,
  };
}

export function mapSettingsFormToPayload(
  form: SettingsForm,
  savedBaseline: SettingsForm | null,
  pendingDeleteSecrets: ConnectionSecretKey[],
): SettingsSaveRequestWithRetention {
  const secrets = Object.fromEntries(
    Object.entries(form.secretInputs).filter(([, value]) => value.trim()),
  ) as SecretsPayload & { XAI_API_KEY?: string };
  const replyStyles =
    form.replyFeatureEnabled && !form.replyStyles.some((style) => style.enabled)
      ? form.replyStyles.map((style, index) => ({
          ...style,
          enabled: index === 0,
        }))
      : form.replyStyles;
  const agents: AgentSettingsPayload = {
    info_enabled: form.infoFeatureEnabled,
  };

  return {
    ...(Object.keys(secrets).length ? { secrets } : {}),
    ...(pendingDeleteSecrets.length
      ? { delete_secrets: pendingDeleteSecrets }
      : {}),
    agents,
    reply: {
      enabled: form.replyFeatureEnabled,
      auto_generate: form.replyAutoGenerate,
      default_style:
        replyStyles.find((style) => style.enabled)?.id ??
        replyStyles[0]?.id ??
        "standard",
      styles: replyStyles.map<ReplyStyleEnabledPatch>((style) => ({
        id: style.id,
        enabled: style.enabled,
      })),
    },
    ollama: { base_url: form.ollamaBaseUrl },
    acp: {
      command: form.acpCommand
        .split(/\r?\n/)
        .filter((argument) => argument.trim()),
    },
    ...(savedBaseline === null ||
    STT_FORM_FIELDS.some((field) => form[field] !== savedBaseline[field])
      ? {
          stt: {
            backend: form.sttBackend,
            whisper_model: form.sttWhisperModel,
            deepgram_model: form.sttDeepgramModel,
            openai_model: form.sttOpenaiModel,
            vosk_model_path: form.sttVoskModelPath,
            language: form.sttLang,
            vad_engine: form.sttVadEngine,
            vad_sensitivity: form.sttVadSensitivity,
            vad_aggressiveness: form.sttVad,
            silence_duration: form.sttSilence,
          },
        }
      : {}),
    context: { dir_override: form.contextDir },
    usage_budget: {
      meeting_limit_jpy: form.usageMeetingLimitJpy,
      monthly_limit_jpy: form.usageMonthlyLimitJpy,
    },
    recording_retention: {
      cutoff_date: form.recordingCleanupCutoffDate || null,
      max_total_bytes:
        form.recordingCleanupMaxMegabytes > 0
          ? Math.floor(form.recordingCleanupMaxMegabytes * 1024 * 1024)
          : null,
    },
  };
}
