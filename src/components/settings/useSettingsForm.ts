import { useEffect, useMemo, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { openUrl } from "@tauri-apps/plugin-opener";
import {
  getOllamaModelsApiSettingsOllamaModelsGet,
  getSettingsApiSettingsGet,
  saveSettingsApiSettingsPost,
  startLoginApiAiRuntimesCodexLoginPost,
  testConnectionApiSettingsConnectionsTestPost,
} from "../../api/generated/sdk.gen";
import type {
  AgentSettingsPayload,
  ReplyStyleEnabledPatch,
  SecretsPayload,
  SettingsResponse,
  SettingsSaveRequest,
  TomlTable,
} from "../../api/generated/types.gen";
import type {
  AiRouteReadModel,
  AiRoutesController,
} from "../../hooks/useAiRoutes";
import {
  useSpeechModel,
  type WhisperModelAlias,
} from "../../hooks/useSpeechModel";
import {
  getManagedAuthStatus,
  getManagedEntitlement,
  openManagedBillingPortal,
  openManagedCheckout,
  startManagedAuth,
} from "../../platform/managedServiceClient";
import { useMeetingStore } from "../../store/meetingStore";
import {
  CONNECTIONS,
  CONNECTION_PROVIDER_BY_ROUTE,
  type ConnectionProvider,
  type ConnectionSecretKey,
  type ConnectionVerification,
} from "./ApiConnectionControl";
import {
  isConnectionUsable,
  type ConnectionUiState,
  type ReplyStyleFormItem,
  type SettingsCategory,
  type SettingsFieldErrors,
  type SettingsForm,
} from "./types";

type SettingsResponseWithRetention = SettingsResponse & {
  recording_retention?: {
    cutoff_date?: string | null;
    max_total_bytes?: number | null;
  };
};

type SettingsSaveRequestWithRetention = SettingsSaveRequest & {
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

const INITIAL_FORM: SettingsForm = {
  secretsStatus: {},
  secretInputs: {},
  ollamaBaseUrl: "http://localhost:11434/v1",
  acpCommand: "",
  sttBackend: "whisper",
  sttWhisperModel: "large-v3-turbo",
  sttDeepgramModel: "nova-2",
  sttOpenaiModel: "gpt-4o-transcribe",
  sttVoskModelPath: "vosk-model-small-ja-0.22",
  sttLang: "ja",
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

const CONNECTION_PROVIDER_BY_STT: Record<string, ConnectionProvider> = {
  deepgram: "deepgram",
  openai: "openai",
  xai: "xai",
};

function tomlString(
  table: TomlTable | undefined,
  key: string,
): string | undefined {
  const value = table?.[key];
  return typeof value === "string" ? value : undefined;
}

function tomlNumber(
  table: TomlTable | undefined,
  key: string,
): number | undefined {
  const value = table?.[key];
  return typeof value === "number" ? value : undefined;
}

function mapResponseToForm(
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

  return {
    secretsStatus: Object.fromEntries(
      SECRET_KEYS.map((key) => [key, secretsStatus[key] ?? false]),
    ),
    secretInputs: {},
    ollamaBaseUrl: settings.ollama?.base_url ?? "http://localhost:11434/v1",
    acpCommand: settings.acp?.command.join("\n") ?? "",
    sttBackend: tomlString(settings.stt, "backend") ?? "whisper",
    sttWhisperModel:
      tomlString(settings.stt, "whisper_model") ?? "large-v3-turbo",
    sttDeepgramModel: tomlString(settings.stt, "deepgram_model") ?? "nova-2",
    sttOpenaiModel:
      tomlString(settings.stt, "openai_model") ?? "gpt-4o-transcribe",
    sttVoskModelPath:
      tomlString(settings.stt, "vosk_model_path") ?? "vosk-model-small-ja-0.22",
    sttLang: tomlString(settings.stt, "language") ?? "ja",
    sttVad: tomlNumber(settings.stt, "vad_aggressiveness") ?? 2,
    sttSilence: tomlNumber(settings.stt, "silence_duration") ?? 0.8,
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

function validateForm(
  form: SettingsForm,
  routes: AiRouteReadModel[],
  connectionStates: Record<ConnectionProvider, ConnectionUiState>,
): SettingsFieldErrors {
  const errors: SettingsFieldErrors = {};
  const routeWithMissingCredential = routes.find((route) => {
    if (route.kind !== "byok") return false;
    const provider =
      CONNECTION_PROVIDER_BY_ROUTE[
        route.id as keyof typeof CONNECTION_PROVIDER_BY_ROUTE
      ];
    return provider ? !isConnectionUsable(connectionStates[provider]) : false;
  });
  if (routeWithMissingCredential) {
    errors.support =
      "この支援方法を利用するには、利用可能なAPIキーが必要です。";
  }
  if (routes.some((route) => route.id === "acp") && !form.acpCommand.trim()) {
    errors.advanced = "ACPを利用するには、起動commandを入力してください。";
  }
  const sttProvider = CONNECTION_PROVIDER_BY_STT[form.sttBackend];
  if (sttProvider && !isConnectionUsable(connectionStates[sttProvider])) {
    errors.audio =
      "クラウド音声認識を利用するには、利用可能なAPIキーが必要です。";
  }
  return errors;
}

function firstErrorCategory(errors: SettingsFieldErrors): SettingsCategory {
  if (errors.support) return "support";
  if (errors.audio) return "audio";
  if (errors.advanced) return "advanced";
  return "privacy";
}

export function useSettingsForm({ routes }: { routes: AiRoutesController }) {
  const [form, setForm] = useState<SettingsForm>(INITIAL_FORM);
  const [savedBaseline, setSavedBaseline] = useState<SettingsForm | null>(null);
  const [activeCategory, setActiveCategory] =
    useState<SettingsCategory>("support");
  const [loaded, setLoaded] = useState(false);
  const [loadingError, setLoadingError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<SettingsFieldErrors>({});
  const [sectionError, setSectionError] = useState<{
    category: SettingsCategory;
    message: string;
  } | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);
  const [ollamaTesting, setOllamaTesting] = useState(false);
  const [ollamaMessage, setOllamaMessage] = useState("");
  const [ollamaMessageIsError, setOllamaMessageIsError] = useState(false);
  const [connectionEditingProvider, setConnectionEditingProvider] =
    useState<ConnectionProvider | null>(null);
  const [pendingDeleteSecrets, setPendingDeleteSecrets] = useState<
    ConnectionSecretKey[]
  >([]);
  const [connectionVerification, setConnectionVerification] = useState<
    Record<ConnectionProvider, ConnectionVerification>
  >({
    openai: "unverified",
    deepgram: "unverified",
    xai: "unverified",
    gemini: "unverified",
    anthropic: "unverified",
  });
  const [connectionTestingProvider, setConnectionTestingProvider] =
    useState<ConnectionProvider | null>(null);
  const [connectionTestMessages, setConnectionTestMessages] = useState<
    Partial<Record<ConnectionProvider, string>>
  >({});

  const speechModelBackend =
    form.sttBackend === "vosk" || form.sttBackend === "whisper"
      ? form.sttBackend
      : null;
  const speechModelLanguage =
    form.sttLang === "ja" || form.sttLang === "en" ? form.sttLang : null;
  const speechModel = useSpeechModel(
    speechModelBackend ?? "vosk",
    speechModelBackend === "whisper"
      ? (form.sttWhisperModel as WhisperModelAlias)
      : null,
    speechModelLanguage,
    loaded && speechModelBackend !== null,
  );

  useEffect(() => {
    let active = true;
    void getSettingsApiSettingsGet()
      .then(({ data, error }) => {
        if (!active) return;
        if (error || !data) {
          setLoadingError(
            "設定を読み込めませんでした。閉じてから再度お試しください。",
          );
          return;
        }
        const loadedForm = mapResponseToForm(data);
        setForm(loadedForm);
        setSavedBaseline(loadedForm);
        setLoaded(true);
      })
      .catch(() => {
        if (active) {
          setLoadingError(
            "設定を読み込めませんでした。閉じてから再度お試しください。",
          );
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const preparedSpeechModelPath =
    speechModel.backend === "vosk" && speechModel.status?.state === "ready"
      ? speechModel.status.model_path
      : null;
  const preparedSpeechModelPathRef = useRef<string | null>(null);
  preparedSpeechModelPathRef.current = preparedSpeechModelPath;
  const lastSyncedSpeechModelPathRef = useRef<string | null>(null);

  useEffect(() => {
    if (!preparedSpeechModelPath) return;
    lastSyncedSpeechModelPathRef.current = preparedSpeechModelPath;
    setForm((previous) =>
      previous.sttVoskModelPath === preparedSpeechModelPath
        ? previous
        : { ...previous, sttVoskModelPath: preparedSpeechModelPath },
    );
    setSavedBaseline((previous) =>
      previous === null || previous.sttVoskModelPath === preparedSpeechModelPath
        ? previous
        : { ...previous, sttVoskModelPath: preparedSpeechModelPath },
    );
  }, [preparedSpeechModelPath]);

  const selectedRoutes = useMemo(
    () =>
      routes.routes.filter(
        (route) =>
          route.id === routes.draftAssignments.reply ||
          route.id === routes.draftAssignments.info ||
          route.id === routes.draftAssignments.minutes,
      ),
    [
      routes.draftAssignments.info,
      routes.draftAssignments.minutes,
      routes.draftAssignments.reply,
      routes.routes,
    ],
  );
  const selectedRoute = selectedRoutes[0] ?? null;
  const busy = savingSettings || routes.saving;
  const formDirty = useMemo(
    () =>
      savedBaseline !== null &&
      JSON.stringify(form) !== JSON.stringify(savedBaseline),
    [form, savedBaseline],
  );
  const hasSecretDraft = Object.values(form.secretInputs).some(
    (value) => value.trim() !== "",
  );
  const dirty =
    formDirty ||
    hasSecretDraft ||
    pendingDeleteSecrets.length > 0 ||
    routes.assignmentDirty;

  const connectionStates = useMemo(
    () =>
      (Object.keys(CONNECTIONS) as ConnectionProvider[]).reduce<
        Record<ConnectionProvider, ConnectionUiState>
      >(
        (states, provider) => {
          const secretKey = CONNECTIONS[provider].secretKey;
          states[provider] = pendingDeleteSecrets.includes(secretKey)
            ? "pending-delete"
            : connectionVerification[provider] === "failed"
              ? "failed"
              : connectionVerification[provider] === "verified"
                ? "verified"
                : form.secretInputs[secretKey]?.trim()
                  ? "draft-unverified"
                  : form.secretsStatus[secretKey]
                    ? "saved-unverified"
                    : "unconfigured";
          return states;
        },
        {} as Record<ConnectionProvider, ConnectionUiState>,
      ),
    [
      connectionVerification,
      form.secretInputs,
      form.secretsStatus,
      pendingDeleteSecrets,
    ],
  );

  const updateForm = <K extends keyof SettingsForm>(
    key: K,
    value: SettingsForm[K],
  ) => {
    const nextForm = { ...form, [key]: value };
    setForm(nextForm);
    setFieldErrors((previous) => ({
      ...previous,
      ...(key === "contextDir" ? { contextDir: undefined } : {}),
    }));
    setSectionError(null);
    setSaveMessage(null);
    if (key === "replyFeatureEnabled" && value === true) {
      const errors = validateForm(nextForm, selectedRoutes, connectionStates);
      if (errors.support) {
        setFieldErrors((previous) => ({
          ...previous,
          support: errors.support,
        }));
        setSectionError({
          category: "support",
          message: "利用する設定に未完了の項目があります。",
        });
      }
    }
  };

  const clearConnectionTestMessage = (provider: ConnectionProvider) => {
    setConnectionTestMessages((previous) => {
      const next = { ...previous };
      delete next[provider];
      return next;
    });
  };

  const beginConnectionEdit = (provider: ConnectionProvider) => {
    setConnectionEditingProvider(provider);
  };

  const cancelConnectionEdit = (provider: ConnectionProvider) => {
    const key = CONNECTIONS[provider].secretKey;
    setForm((previous) => ({
      ...previous,
      secretInputs: { ...previous.secretInputs, [key]: "" },
    }));
    setConnectionEditingProvider((previous) =>
      previous === provider ? null : previous,
    );
    setConnectionVerification((previous) => ({
      ...previous,
      [provider]: "unverified",
    }));
    clearConnectionTestMessage(provider);
  };

  const updateSecret = (provider: ConnectionProvider, value: string): void => {
    const key = CONNECTIONS[provider].secretKey;
    setForm((previous) => ({
      ...previous,
      secretInputs: { ...previous.secretInputs, [key]: value },
    }));
    setConnectionVerification((previous) => ({
      ...previous,
      [provider]: "unverified",
    }));
    clearConnectionTestMessage(provider);
    setPendingDeleteSecrets((previous) =>
      previous.filter((secret) => secret !== key),
    );
    setFieldErrors((previous) => ({
      ...previous,
      audio: undefined,
      support: undefined,
      advanced: undefined,
    }));
    setSectionError(null);
    setSaveMessage(null);
  };

  const testConnection = async (
    provider: ConnectionProvider,
  ): Promise<void> => {
    if (connectionTestingProvider !== null) return;
    const secretKey = CONNECTIONS[provider].secretKey;
    const draftKey = form.secretInputs[secretKey]?.trim();
    if (!form.secretsStatus[secretKey] && !draftKey) return;
    setConnectionTestingProvider(provider);
    clearConnectionTestMessage(provider);
    try {
      const { data, error } =
        await testConnectionApiSettingsConnectionsTestPost({
          body: {
            provider,
            ...(draftKey ? { api_key: draftKey } : {}),
          },
        });
      const verified = Boolean(
        data?.ok && data.status === "verified" && !error,
      );
      setConnectionVerification((previous) => ({
        ...previous,
        [provider]: verified ? "verified" : "failed",
      }));
      setConnectionTestMessages((previous) => ({
        ...previous,
        [provider]:
          data?.message ??
          (verified ? "接続を確認しました。" : "接続を確認できませんでした。"),
      }));
    } catch {
      setConnectionVerification((previous) => ({
        ...previous,
        [provider]: "failed",
      }));
      setConnectionTestMessages((previous) => ({
        ...previous,
        [provider]:
          "接続を確認できませんでした。APIキーとネットワークを確認してください。",
      }));
    } finally {
      setConnectionTestingProvider(null);
    }
  };

  const scheduleSecretDeletion = (provider: ConnectionProvider) => {
    const key = CONNECTIONS[provider].secretKey;
    setPendingDeleteSecrets((previous) =>
      previous.includes(key) ? previous : [...previous, key],
    );
    setForm((previous) => ({
      ...previous,
      secretInputs: { ...previous.secretInputs, [key]: "" },
    }));
    setConnectionVerification((previous) => ({
      ...previous,
      [provider]: "unverified",
    }));
    setConnectionEditingProvider(null);
    clearConnectionTestMessage(provider);
  };

  const cancelSecretDeletion = (provider: ConnectionProvider) => {
    const key = CONNECTIONS[provider].secretKey;
    setPendingDeleteSecrets((previous) =>
      previous.filter((secret) => secret !== key),
    );
  };

  const assignRoute = (
    useCase: keyof typeof routes.draftAssignments,
    routeId: string | null,
  ) => {
    routes.setDraftAssignment(useCase, routeId);
    setFieldErrors({});
    setSectionError(null);
    setSaveMessage(null);
  };

  const chooseContextDirectory = async () => {
    try {
      const directory = await open({
        directory: true,
        multiple: false,
        title: "会議の前提資料フォルダを選択",
      });
      if (directory) updateForm("contextDir", directory);
    } catch {
      setSectionError({
        category: "privacy",
        message: "フォルダを開けませんでした。もう一度お試しください。",
      });
    }
  };

  const handleRouteAction = async (route: AiRouteReadModel) => {
    if (route.action === "sign_in") {
      try {
        await startManagedAuth();
      } catch {
        setSectionError({
          category: "support",
          message:
            "ログインを開始できませんでした。アカウント設定を確認してください。",
        });
      }
      return;
    }
    if (route.action === "subscribe" || route.action === "manage_billing") {
      try {
        if (route.action === "subscribe") await openManagedCheckout();
        else await openManagedBillingPortal();
      } catch {
        setSectionError({
          category: "support",
          message:
            "プラン管理を開けませんでした。アカウント設定を確認してください。",
        });
      }
      return;
    }
    if (route.action === "view_usage") {
      setActiveCategory("account");
      return;
    }
    if (route.action === "retry") {
      await routes.reload();
      return;
    }
    if (route.action === "install") {
      try {
        await openUrl("https://developers.openai.com/codex/cli/");
      } catch {
        setSectionError({
          category: "support",
          message:
            "ブラウザを開けませんでした。既定のブラウザ設定を確認してください。",
        });
      }
      return;
    }
    if (route.action !== "login") return;
    try {
      const { data, error } = await startLoginApiAiRuntimesCodexLoginPost();
      if (error || !data) throw new Error("login unavailable");
      const authUrl = new URL(data.auth_url);
      if (authUrl.protocol !== "https:") throw new Error("unsafe login URL");
      await openUrl(authUrl.href);
    } catch {
      setSectionError({
        category: "support",
        message:
          "ログインを開始できませんでした。状態を再確認してからもう一度お試しください。",
      });
    }
  };

  const testOllamaConnection = async () => {
    if (!form.ollamaBaseUrl.trim()) {
      setOllamaMessage("ベースURLを入力してください。");
      setOllamaMessageIsError(true);
      return;
    }
    setOllamaTesting(true);
    setOllamaMessage("");
    try {
      const { data, error } = await getOllamaModelsApiSettingsOllamaModelsGet({
        query: { base_url: form.ollamaBaseUrl },
      });
      if (error || !data?.ok) {
        setOllamaMessage(
          data?.message ??
            "接続できませんでした。URLとOllamaの起動状態を確認してください。",
        );
        setOllamaMessageIsError(true);
        return;
      }
      setOllamaMessage(
        data.models.length
          ? `${data.models.length}件のモデルを確認しました。`
          : "接続できましたが、モデルがありません。",
      );
      setOllamaMessageIsError(false);
    } catch {
      setOllamaMessage(
        "接続できませんでした。URLとOllamaの起動状態を確認してください。",
      );
      setOllamaMessageIsError(true);
    } finally {
      setOllamaTesting(false);
    }
  };

  const settingsPayload = useMemo<SettingsSaveRequestWithRetention>(() => {
    const secrets = Object.fromEntries(
      Object.entries(form.secretInputs).filter(([, value]) => value.trim()),
    ) as SecretsPayload & { XAI_API_KEY?: string };
    const replyStyles =
      form.replyFeatureEnabled &&
      !form.replyStyles.some((style) => style.enabled)
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
      stt: {
        backend: form.sttBackend,
        whisper_model: form.sttWhisperModel,
        deepgram_model: form.sttDeepgramModel,
        openai_model: form.sttOpenaiModel,
        vosk_model_path: form.sttVoskModelPath,
        language: form.sttLang,
        vad_aggressiveness: form.sttVad,
        silence_duration: form.sttSilence,
      },
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
  }, [form, pendingDeleteSecrets]);

  const save = async () => {
    if (speechModel.blocksSettingsSave) return;
    if (form.sttBackend === "managed") {
      try {
        const auth = await getManagedAuthStatus();
        const entitlement = auth.authenticated
          ? await getManagedEntitlement()
          : null;
        if (
          !entitlement ||
          entitlement.managed.readiness !== "ready" ||
          !entitlement.managed.speech_recognition.selectable
        ) {
          throw new Error("managed speech recognition unavailable");
        }
      } catch {
        const message =
          "Meeting Supporter 音声認識を利用できません。アカウントとプランを確認してください。";
        setFieldErrors({ audio: message });
        setSectionError({ category: "audio", message });
        setSaveMessage(null);
        setActiveCategory("audio");
        return;
      }
    }
    const errors = validateForm(form, selectedRoutes, connectionStates);
    setFieldErrors(errors);
    setSaveMessage(null);
    setSectionError(null);
    if (Object.values(errors).some(Boolean)) {
      const category = firstErrorCategory(errors);
      setSectionError({
        category,
        message: "保存する前に、入力が必要な項目を確認してください。",
      });
      setActiveCategory(category);
      return;
    }

    const preparedPathAtSaveStart =
      speechModel.backend === "vosk"
        ? preparedSpeechModelPathRef.current
        : null;
    const speechModelPathForSave =
      preparedPathAtSaveStart &&
      lastSyncedSpeechModelPathRef.current !== preparedPathAtSaveStart
        ? preparedPathAtSaveStart
        : form.sttVoskModelPath;
    const settingsPayloadForSave: SettingsSaveRequestWithRetention = {
      ...settingsPayload,
      stt: {
        ...settingsPayload.stt,
        vosk_model_path: speechModelPathForSave,
      },
    };
    setSavingSettings(true);
    try {
      const { data, error } = await saveSettingsApiSettingsPost({
        body: settingsPayloadForSave,
      });
      if (error || !data?.ok) {
        setSectionError({
          category: activeCategory,
          message:
            "この項目を保存できませんでした。内容を確認して再度お試しください。",
        });
        return;
      }

      const savedForm = mapResponseToForm(
        data.settings as SettingsResponseWithRetention,
      );
      const latestPreparedPath =
        speechModel.backend === "vosk"
          ? preparedSpeechModelPathRef.current
          : null;
      const synchronizedPath =
        latestPreparedPath && latestPreparedPath !== preparedPathAtSaveStart
          ? latestPreparedPath
          : speechModelPathForSave;
      const nextForm =
        speechModel.backend === "vosk"
          ? { ...savedForm, sttVoskModelPath: synchronizedPath }
          : savedForm;
      setForm(nextForm);
      setSavedBaseline(nextForm);
      setPendingDeleteSecrets([]);
      setConnectionEditingProvider(null);
      setConnectionTestMessages({});
      const savedBackend = tomlString(data.settings.stt, "backend");
      if (savedBackend) useMeetingStore.setState({ sttBackend: savedBackend });

      await routes.reload();
      const routeSaved = await routes.saveAssignments();
      if (!routeSaved) {
        setSaveMessage(
          "その他の設定は保存済み。AI機能の割り当てのみ保存できませんでした",
        );
        setActiveCategory("support");
        return;
      }
      setSaveMessage("設定を保存しました。");
    } catch {
      setSectionError({
        category: activeCategory,
        message:
          "設定を保存できませんでした。しばらくしてから再度お試しください。",
      });
    } finally {
      setSavingSettings(false);
    }
  };

  const discardChanges = () => {
    if (savedBaseline) setForm(savedBaseline);
    setPendingDeleteSecrets([]);
    setConnectionEditingProvider(null);
    setConnectionTestMessages({});
    setConnectionVerification({
      openai: "unverified",
      deepgram: "unverified",
      xai: "unverified",
      gemini: "unverified",
      anthropic: "unverified",
    });
    setFieldErrors({});
    setSectionError(null);
    setSaveMessage(null);
    routes.resetDraftAssignments();
  };

  return {
    form,
    activeCategory,
    setActiveCategory,
    loaded,
    loadingError,
    fieldErrors,
    sectionError,
    saveMessage,
    clearSaveMessage: () => setSaveMessage(null),
    busy,
    dirty,
    ollamaTesting,
    ollamaMessage,
    ollamaMessageIsError,
    connectionEditingProvider,
    connectionTestingProvider,
    connectionTestMessages,
    speechModel,
    selectedRoute,
    connectionStates,
    updateForm,
    updateSecret,
    beginConnectionEdit,
    cancelConnectionEdit,
    testConnection,
    scheduleSecretDeletion,
    cancelSecretDeletion,
    assignRoute,
    chooseContextDirectory,
    handleRouteAction,
    testOllamaConnection,
    save,
    discardChanges,
  };
}

export type SettingsFormController = ReturnType<typeof useSettingsForm>;
