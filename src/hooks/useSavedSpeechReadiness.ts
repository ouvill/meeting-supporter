import { useEffect, useState } from "react";
import { getSettingsApiSettingsGet } from "../api/generated/sdk.gen";
import { mapSettingsResponseToForm } from "../components/settings/settingsFormMapping";
import type { SettingsForm } from "../components/settings/types";
import { useSpeechModel, isWhisperModelAlias } from "./useSpeechModel";

export type SavedSpeechReadiness =
  | "checking"
  | "ready"
  | "missing"
  | "downloading"
  | "error";

export function useSavedSpeechReadiness(
  settingsOpen: boolean,
  connected: boolean,
): SavedSpeechReadiness {
  const [configuration, setConfiguration] = useState<SettingsForm | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (settingsOpen || !connected) return;
    const controller = new AbortController();
    setConfiguration(null);
    setFailed(false);
    void getSettingsApiSettingsGet({ signal: controller.signal })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (error || !data) setFailed(true);
        else setConfiguration(mapSettingsResponseToForm(data));
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [settingsOpen, connected]);
  const backend = configuration?.sttBackend;
  const local = backend === "whisper" || backend === "reazonspeech";
  const whisperModel = isWhisperModelAlias(configuration?.sttWhisperModel)
    ? configuration.sttWhisperModel
    : null;
  const validModel = backend !== "whisper" || whisperModel !== null;
  const language =
    configuration?.sttLang === "en"
      ? "en"
      : configuration?.sttLang === "ja" ||
          (backend === "whisper" && configuration?.sttLang === "auto")
        ? "ja"
        : null;
  const model = useSpeechModel(
    backend === "whisper" ? "whisper" : "reazonspeech",
    backend === "whisper" ? whisperModel : null,
    language,
    local && validModel && !settingsOpen && connected,
  );
  if (failed) return "error";
  if (!configuration) return "checking";
  if (!local) return backend === "dummy" ? "ready" : "error";
  if (!validModel || language === null) return "error";
  if (model.loading || model.confirmingStart || model.action === "starting")
    return "checking";
  if (model.error || !model.status) return "error";
  if (model.status.state === "ready") return "ready";
  if (model.status.state === "downloading") return "downloading";
  return "missing";
}
