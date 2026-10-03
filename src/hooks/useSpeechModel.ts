import { useEffect, useMemo } from "react";
import { useStore } from "zustand";
import type { SpeechModelStatusResponse } from "../api/generated/types.gen";
import { getSpeechModelTask } from "../store/speechModelStore";

export type SpeechModelLanguage = "ja" | "en";
export type SpeechModelBackend = "whisper" | "reazonspeech";
export type WhisperModelAlias =
  | "tiny"
  | "base"
  | "small"
  | "medium"
  | "large-v2"
  | "large-v3-turbo";
export type SpeechModelAction = "starting" | "cancelling" | null;

export function isWhisperModelAlias(
  value: unknown,
): value is WhisperModelAlias {
  return (
    typeof value === "string" &&
    ["tiny", "base", "small", "medium", "large-v2", "large-v3-turbo"].includes(
      value,
    )
  );
}

export type SpeechModelStatus = SpeechModelStatusResponse;

export interface SpeechModelController {
  backend: SpeechModelBackend;
  model: WhisperModelAlias | null;
  language: SpeechModelLanguage | null;
  status: SpeechModelStatus | null;
  loading: boolean;
  action: SpeechModelAction;
  error: string | null;
  confirmingStart: boolean;
  checkingStatus: boolean;
  isDownloading: boolean;
  blocksSettingsSave: boolean;
  refresh: () => Promise<void>;
  startDownload: () => Promise<void>;
  cancelDownload: () => Promise<void>;
}

export function useSpeechModel(
  backend: SpeechModelBackend,
  model: WhisperModelAlias | null,
  language: SpeechModelLanguage | null,
  enabled = true,
): SpeechModelController {
  const task = useMemo(
    () => getSpeechModelTask(backend, model, language),
    [backend, model, language],
  );
  const state = useStore(task.store);
  const active = enabled && language !== null;
  useEffect(() => {
    if (active) return task.attach();
  }, [active, task]);
  const status = active ? state.status : null;
  const action = active ? state.action : null;
  const confirmingStart = active && state.confirmingStart;
  const checkingStatus = active && state.checkingStatus;
  const isDownloading = status?.state === "downloading";
  return {
    backend,
    model,
    language,
    status,
    action,
    error: active ? state.error : null,
    confirmingStart,
    checkingStatus,
    isDownloading,
    loading: checkingStatus,
    blocksSettingsSave:
      checkingStatus || isDownloading || action !== null || confirmingStart,
    refresh: async () => {
      if (active) await task.refresh();
    },
    startDownload: async () => {
      if (active) await task.startDownload();
    },
    cancelDownload: async () => {
      if (active) await task.cancelDownload();
    },
  };
}
