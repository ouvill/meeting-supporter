import { useMemo } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import type { AiRoutesController } from "../../hooks/useAiRoutes";
import {
  useSpeechModel,
  type WhisperModelAlias,
} from "../../hooks/useSpeechModel";
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
  const { form, setForm, setFieldErrors, setSectionError, setSaveMessage } =
    persistence;
  const connections = useConnectionSettings({
    form,
    setForm,
    setFieldErrors,
    setSectionError,
    setSaveMessage,
  });

  const speechModelBackend =
    form.sttBackend === "whisper" || form.sttBackend === "reazonspeech"
      ? form.sttBackend
      : null;
  // Whisper uses the same multilingual model for automatic language detection.
  const speechModelLanguage =
    form.sttLang === "ja" || form.sttLang === "en"
      ? form.sttLang
      : form.sttBackend === "whisper" && form.sttLang === "auto"
        ? "ja"
        : null;
  const speechModel = useSpeechModel(
    speechModelBackend ?? "reazonspeech",
    speechModelBackend === "whisper"
      ? (form.sttWhisperModel as WhisperModelAlias)
      : null,
    speechModelLanguage,
    persistence.loaded && speechModelBackend !== null,
  );
  const selectedRoutes = useMemo(
    () =>
      routes.routes.filter(
        (route) => route.id === routes.draftAssignments.reply,
      ),
    [routes.draftAssignments.reply, routes.routes],
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

  const save = () =>
    persistence.save({
      blocksSettingsSave: speechModel.blocksSettingsSave,
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
    connectionEditingProvider: connections.connectionEditingProvider,
    connectionTestingProvider: connections.connectionTestingProvider,
    connectionTestMessages: connections.connectionTestMessages,
    speechModel,
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
    save,
    discardChanges,
  };
}

export type SettingsFormController = ReturnType<typeof useSettingsForm>;
