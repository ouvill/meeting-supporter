import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  getSettingsApiSettingsGet,
  saveSettingsApiSettingsPost,
} from "../../api/generated/sdk.gen";
import type { AiRouteReadModel, AiRoutesController } from "../../hooks/useAiRoutes";
import {
  getManagedAuthStatus,
  getManagedEntitlement,
} from "../../platform/managedServiceClient";
import { useMeetingStore } from "../../store/meetingStore";
import type { ConnectionProvider, ConnectionSecretKey } from "./ApiConnectionControl";
import {
  getTomlString,
  INITIAL_SETTINGS_FORM,
  mapSettingsFormToPayload,
  mapSettingsResponseToForm,
  type SettingsResponseWithRetention,
  type SettingsSaveRequestWithRetention,
} from "./settingsFormMapping";
import {
  firstSettingsErrorCategory,
  validateSettingsForm,
} from "./settingsValidation";
import type {
  ConnectionUiState,
  SettingsCategory,
  SettingsFieldErrors,
  SettingsForm,
} from "./types";

export type SettingsSectionError = {
  category: SettingsCategory;
  message: string;
} | null;

interface SaveSettingsContext {
  blocksSettingsSave: boolean;
  speechModelBackend: string | null;
  selectedRoutes: AiRouteReadModel[];
  connectionStates: Record<ConnectionProvider, ConnectionUiState>;
  pendingDeleteSecrets: ConnectionSecretKey[];
  resetConnectionsAfterSave: () => void;
}

export function useSettingsPersistence({
  routes,
  audioSettingsLocked,
}: {
  routes: AiRoutesController;
  audioSettingsLocked: boolean;
}) {
  const [form, setForm] = useState<SettingsForm>(INITIAL_SETTINGS_FORM);
  const [savedBaseline, setSavedBaseline] = useState<SettingsForm | null>(null);
  const [activeCategory, setActiveCategory] =
    useState<SettingsCategory>("support");
  const [loaded, setLoaded] = useState(false);
  const [loadingError, setLoadingError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<SettingsFieldErrors>({});
  const [sectionError, setSectionError] =
    useState<SettingsSectionError>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);
  const previousAudioSettingsLocked = useRef(audioSettingsLocked);
  const preparedSpeechModelPathRef = useRef<string | null>(null);
  const lastSyncedSpeechModelPathRef = useRef<string | null>(null);

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
        const loadedForm = mapSettingsResponseToForm(data);
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

  useEffect(() => {
    if (!audioSettingsLocked) {
      previousAudioSettingsLocked.current = false;
      return;
    }
    if (previousAudioSettingsLocked.current || savedBaseline === null) return;

    previousAudioSettingsLocked.current = true;
    setForm((previous) => ({
      ...previous,
      sttBackend: savedBaseline.sttBackend,
      sttWhisperModel: savedBaseline.sttWhisperModel,
      sttDeepgramModel: savedBaseline.sttDeepgramModel,
      sttOpenaiModel: savedBaseline.sttOpenaiModel,
      sttVoskModelPath: savedBaseline.sttVoskModelPath,
      sttLang: savedBaseline.sttLang,
      sttVadEngine: savedBaseline.sttVadEngine,
      sttVadSensitivity: savedBaseline.sttVadSensitivity,
      sttVad: savedBaseline.sttVad,
      sttSilence: savedBaseline.sttSilence,
    }));
  }, [audioSettingsLocked, savedBaseline]);

  const formDirty = useMemo(
    () =>
      savedBaseline !== null &&
      JSON.stringify(form) !== JSON.stringify(savedBaseline),
    [form, savedBaseline],
  );

  const updateForm = <K extends keyof SettingsForm>(
    key: K,
    value: SettingsForm[K],
    selectedRoutes: AiRouteReadModel[],
    connectionStates: Record<ConnectionProvider, ConnectionUiState>,
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
      const errors = validateSettingsForm(
        nextForm,
        selectedRoutes,
        connectionStates,
      );
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

  const trackPreparedSpeechModelPath = (path: string | null) => {
    preparedSpeechModelPathRef.current = path;
  };

  const synchronizePreparedSpeechModelPath = useCallback(
    (preparedSpeechModelPath: string | null) => {
      if (!preparedSpeechModelPath) return;
      lastSyncedSpeechModelPathRef.current = preparedSpeechModelPath;
      setForm((previous) =>
        previous.sttVoskModelPath === preparedSpeechModelPath
          ? previous
          : { ...previous, sttVoskModelPath: preparedSpeechModelPath },
      );
      setSavedBaseline((previous) =>
        previous === null ||
        previous.sttVoskModelPath === preparedSpeechModelPath
          ? previous
          : { ...previous, sttVoskModelPath: preparedSpeechModelPath },
      );
    },
    [],
  );

  const save = async ({
    blocksSettingsSave,
    speechModelBackend,
    selectedRoutes,
    connectionStates,
    pendingDeleteSecrets,
    resetConnectionsAfterSave,
  }: SaveSettingsContext) => {
    if (blocksSettingsSave && !audioSettingsLocked) return;
    const settingsPayload = mapSettingsFormToPayload(
      form,
      savedBaseline,
      pendingDeleteSecrets,
    );
    if (settingsPayload.stt && form.sttBackend === "managed") {
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
    const errors = validateSettingsForm(
      form,
      selectedRoutes,
      connectionStates,
    );
    setFieldErrors(errors);
    setSaveMessage(null);
    setSectionError(null);
    if (Object.values(errors).some(Boolean)) {
      const category = firstSettingsErrorCategory(errors);
      setSectionError({
        category,
        message: "保存する前に、入力が必要な項目を確認してください。",
      });
      setActiveCategory(category);
      return;
    }

    const preparedPathAtSaveStart =
      speechModelBackend === "vosk"
        ? preparedSpeechModelPathRef.current
        : null;
    const speechModelPathForSave =
      preparedPathAtSaveStart &&
      lastSyncedSpeechModelPathRef.current !== preparedPathAtSaveStart
        ? preparedPathAtSaveStart
        : form.sttVoskModelPath;
    const settingsPayloadForSave: SettingsSaveRequestWithRetention =
      settingsPayload.stt
        ? {
            ...settingsPayload,
            stt: {
              ...settingsPayload.stt,
              vosk_model_path: speechModelPathForSave,
            },
          }
        : settingsPayload;
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

      const savedForm = mapSettingsResponseToForm(
        data.settings as SettingsResponseWithRetention,
      );
      const latestPreparedPath =
        speechModelBackend === "vosk"
          ? preparedSpeechModelPathRef.current
          : null;
      const synchronizedPath =
        latestPreparedPath && latestPreparedPath !== preparedPathAtSaveStart
          ? latestPreparedPath
          : speechModelPathForSave;
      const nextForm =
        speechModelBackend === "vosk"
          ? { ...savedForm, sttVoskModelPath: synchronizedPath }
          : savedForm;
      setForm(nextForm);
      setSavedBaseline(nextForm);
      resetConnectionsAfterSave();
      const savedBackend = getTomlString(data.settings.stt, "backend");
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

  const discardChanges = (resetConnectionsAfterDiscard: () => void) => {
    if (savedBaseline) setForm(savedBaseline);
    resetConnectionsAfterDiscard();
    setFieldErrors({});
    setSectionError(null);
    setSaveMessage(null);
    routes.resetDraftAssignments();
  };

  return {
    form,
    setForm,
    savedBaseline,
    activeCategory,
    setActiveCategory,
    loaded,
    loadingError,
    fieldErrors,
    setFieldErrors,
    sectionError,
    setSectionError,
    saveMessage,
    setSaveMessage,
    clearSaveMessage: () => setSaveMessage(null),
    savingSettings,
    formDirty,
    updateForm,
    trackPreparedSpeechModelPath,
    synchronizePreparedSpeechModelPath,
    save,
    discardChanges,
  };
}
