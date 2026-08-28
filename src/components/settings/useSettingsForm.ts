import { useEffect, useMemo, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { openUrl } from "@tauri-apps/plugin-opener";
import {
  getOllamaModelsApiSettingsOllamaModelsGet,
  startLoginApiAiRuntimesCodexLoginPost,
} from "../../api/generated/sdk.gen";
import type {
  AiRouteReadModel,
  AiRoutesController,
} from "../../hooks/useAiRoutes";
import {
  useSpeechModel,
  type WhisperModelAlias,
} from "../../hooks/useSpeechModel";
import {
  openManagedBillingPortal,
  openManagedCheckout,
  startManagedAuth,
} from "../../platform/managedServiceClient";
import { useConnectionSettings } from "./useConnectionSettings";
import { useSettingsPersistence } from "./useSettingsPersistence";
import type { SettingsForm } from "./types";

export function useSettingsForm({
  routes,
  audioSettingsLocked,
}: {
  routes: AiRoutesController;
  audioSettingsLocked: boolean;
}) {
  const persistence = useSettingsPersistence({ routes, audioSettingsLocked });
  const {
    form,
    setForm,
    savedBaseline,
    setFieldErrors,
    setSectionError,
    setSaveMessage,
  } = persistence;
  const connections = useConnectionSettings({
    form,
    setForm,
    savedBaseline,
    audioSettingsLocked,
    setFieldErrors,
    setSectionError,
    setSaveMessage,
  });
  const [ollamaTesting, setOllamaTesting] = useState(false);
  const [ollamaMessage, setOllamaMessage] = useState("");
  const [ollamaMessageIsError, setOllamaMessageIsError] = useState(false);

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
    persistence.loaded && speechModelBackend !== null,
  );
  const preparedSpeechModelPath =
    speechModel.backend === "vosk" && speechModel.status?.state === "ready"
      ? speechModel.status.model_path
      : null;
  persistence.trackPreparedSpeechModelPath(preparedSpeechModelPath);

  useEffect(() => {
    persistence.synchronizePreparedSpeechModelPath(preparedSpeechModelPath);
  }, [
    persistence.synchronizePreparedSpeechModelPath,
    preparedSpeechModelPath,
  ]);

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
  const busy = persistence.savingSettings || routes.saving;
  const hasSecretDraft = Object.values(form.secretInputs).some(
    (value) => value.trim() !== "",
  );
  const dirty =
    persistence.formDirty ||
    hasSecretDraft ||
    connections.pendingDeleteSecrets.length > 0 ||
    routes.assignmentDirty;
  const currentSttBackend =
    audioSettingsLocked && savedBaseline !== null
      ? savedBaseline.sttBackend
      : form.sttBackend;

  const updateForm = <K extends keyof SettingsForm>(
    key: K,
    value: SettingsForm[K],
  ) => {
    persistence.updateForm(
      key,
      value,
      selectedRoutes,
      connections.connectionStates,
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
      persistence.setActiveCategory("account");
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

  const save = () =>
    persistence.save({
      blocksSettingsSave: speechModel.blocksSettingsSave,
      speechModelBackend: speechModel.backend,
      selectedRoutes,
      connectionStates: connections.connectionStates,
      pendingDeleteSecrets: connections.pendingDeleteSecrets,
      resetConnectionsAfterSave: connections.resetAfterSave,
    });

  const discardChanges = () => {
    persistence.discardChanges(connections.resetAfterDiscard);
  };

  return {
    form,
    activeCategory: persistence.activeCategory,
    setActiveCategory: persistence.setActiveCategory,
    loaded: persistence.loaded,
    loadingError: persistence.loadingError,
    fieldErrors: persistence.fieldErrors,
    sectionError: persistence.sectionError,
    saveMessage: persistence.saveMessage,
    clearSaveMessage: persistence.clearSaveMessage,
    busy,
    dirty,
    ollamaTesting,
    ollamaMessage,
    ollamaMessageIsError,
    connectionEditingProvider: connections.connectionEditingProvider,
    connectionTestingProvider: connections.connectionTestingProvider,
    connectionTestMessages: connections.connectionTestMessages,
    speechModel,
    currentSttBackend,
    selectedRoute,
    connectionStates: connections.connectionStates,
    updateForm,
    updateSecret: connections.updateSecret,
    beginConnectionEdit: connections.beginConnectionEdit,
    cancelConnectionEdit: connections.cancelConnectionEdit,
    testConnection: connections.testConnection,
    scheduleSecretDeletion: connections.scheduleSecretDeletion,
    cancelSecretDeletion: connections.cancelSecretDeletion,
    assignRoute,
    chooseContextDirectory,
    handleRouteAction,
    testOllamaConnection,
    save,
    discardChanges,
  };
}

export type SettingsFormController = ReturnType<typeof useSettingsForm>;
