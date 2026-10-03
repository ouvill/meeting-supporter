import { createStore } from "zustand/vanilla";
import {
  cancelSpeechModelDownloadApiSttModelCancelPost,
  getSpeechModelStatusApiSttModelGet,
  startSpeechModelDownloadApiSttModelDownloadPost,
} from "../api/generated/sdk.gen";
import type {
  SpeechModelAction,
  SpeechModelBackend,
  SpeechModelLanguage,
  SpeechModelStatus,
  WhisperModelAlias,
} from "../hooks/useSpeechModel";
import { useSettingsTaskStore } from "./settingsTaskStore";

interface ModelState {
  status: SpeechModelStatus | null;
  action: SpeechModelAction;
  error: string | null;
  confirmingStart: boolean;
  checkingStatus: boolean;
}

const INITIAL: ModelState = {
  status: null,
  action: null,
  error: null,
  confirmingStart: false,
  checkingStatus: true,
};
const POLL_INTERVAL_MS = 800;
const STATUS_ERROR =
  "準備状況を確認できませんでした。通信状態を確認して、もう一度お試しください。";
const POLL_ERROR =
  "準備状況を更新できませんでした。取得は続いているため、自動で再確認します。";
const START_ERROR =
  "取得を始められませんでした。通信状態を確認して、もう一度お試しください。";
const CANCEL_ERROR =
  "取得を取り消せませんでした。通信状態を確認して、もう一度お試しください。";
const START_CONFIRM_ERROR =
  "取得を開始できたか確認しています。通信が戻ると自動で更新します。";

function createSpeechModelTask(
  backend: SpeechModelBackend,
  model: WhisperModelAlias | null,
  language: SpeechModelLanguage | null,
) {
  const store = createStore<ModelState>(() => ({ ...INITIAL }));
  const request =
    language === null
      ? null
      : { backend, language, ...(model ? { model } : {}) };
  const id = `speech:${backend}:${model ?? ""}:${language ?? ""}`;
  const label = `${backend === "whisper" ? `Whisper ${model ?? ""}`.trim() : "ReazonSpeech"}（${language === "en" ? "英語" : "日本語"}）`;
  let subscribers = 0;
  let tracked = false;
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let readController: AbortController | null = null;
  let actionController: AbortController | null = null;

  const running = () => {
    const state = store.getState();
    return (
      state.action !== null ||
      state.confirmingStart ||
      state.status?.state === "downloading"
    );
  };
  const matches = (status: SpeechModelStatus) =>
    status.backend === backend &&
    status.language === language &&
    (model === null || status.model_id === model);

  const report = () => {
    const { status, action, error, confirmingStart } = store.getState();
    if (status?.state === "downloading") tracked = true;
    if (!tracked) return;
    const active = running();
    const failed =
      !active && status?.state !== "ready" && status?.state !== "cancelled";
    const percent =
      status?.progress_percent ??
      (status?.total_bytes
        ? (status.downloaded_bytes / status.total_bytes) * 100
        : null);
    const progress =
      percent !== null && Number.isFinite(percent)
        ? ` ${Math.round(Math.max(0, Math.min(100, percent)))}%`
        : "";
    const message = active
      ? (error ??
        (confirmingStart
          ? START_CONFIRM_ERROR
          : action === "cancelling"
            ? "取得を取り消しています。"
            : `取得中${progress}。設定を閉じても処理は続きます。`))
      : status?.state === "ready"
        ? "取得が完了しました。"
        : status?.state === "cancelled"
          ? "取得を取り消しました。"
          : (error ?? (status?.message || START_ERROR));
    useSettingsTaskStore.getState().report({
      id,
      label: `${label}の取得`,
      state: active ? "running" : failed ? "failed" : "completed",
      message: `${label}: ${message}`,
    });
    if (!active) tracked = false;
  };
  const stopReading = () => {
    clearTimeout(timer);
    readController?.abort();
    readController = null;
  };
  const schedule = () => {
    clearTimeout(timer);
    if (!disposed && running() && store.getState().action === null)
      timer = setTimeout(() => void refresh(), POLL_INTERVAL_MS);
  };

  const refresh = async () => {
    if (disposed || request === null || store.getState().action !== null)
      return;
    stopReading();
    const controller = new AbortController();
    readController = controller;
    const { confirmingStart, status } = store.getState();
    const errorMessage = confirmingStart
      ? START_CONFIRM_ERROR
      : status?.state === "downloading"
        ? POLL_ERROR
        : STATUS_ERROR;
    if (!status) store.setState({ checkingStatus: true });
    try {
      const result = await getSpeechModelStatusApiSttModelGet({
        query: request,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if (result.error || !result.data || !matches(result.data)) {
        store.setState({ error: errorMessage });
      } else {
        store.setState({
          status: result.data,
          confirmingStart: false,
          error:
            confirmingStart &&
            result.data.state !== "downloading" &&
            result.data.state !== "ready"
              ? START_ERROR
              : null,
        });
      }
    } catch {
      if (!controller.signal.aborted) store.setState({ error: errorMessage });
    } finally {
      if (!controller.signal.aborted) {
        readController = null;
        store.setState({ checkingStatus: false });
        report();
        schedule();
      }
    }
  };

  const perform = async (action: Exclude<SpeechModelAction, null>) => {
    const { status } = store.getState();
    if (disposed || request === null || store.getState().action !== null)
      return;
    if (action === "starting" && running()) return;
    if (
      action === "cancelling" &&
      (status?.state !== "downloading" || !status.cancelable)
    )
      return;
    stopReading();
    const controller = new AbortController();
    actionController = controller;
    tracked = true;
    store.setState({ action, error: null });
    report();
    try {
      const result =
        action === "starting"
          ? await startSpeechModelDownloadApiSttModelDownloadPost({
              body: request,
              signal: controller.signal,
            })
          : await cancelSpeechModelDownloadApiSttModelCancelPost({
              query: request,
              signal: controller.signal,
            });
      if (controller.signal.aborted) return;
      if (result.error || !result.data || !matches(result.data)) {
        store.setState(
          action === "starting"
            ? { confirmingStart: true, error: START_CONFIRM_ERROR }
            : { error: CANCEL_ERROR },
        );
      } else {
        store.setState({ status: result.data, confirmingStart: false });
      }
    } catch {
      if (!controller.signal.aborted)
        store.setState(
          action === "starting"
            ? { confirmingStart: true, error: START_CONFIRM_ERROR }
            : { error: CANCEL_ERROR },
        );
    } finally {
      if (!controller.signal.aborted) {
        actionController = null;
        store.setState({ action: null, checkingStatus: false });
        report();
        schedule();
      }
    }
  };

  return {
    store,
    refresh,
    startDownload: () => perform("starting"),
    cancelDownload: () => perform("cancelling"),
    attach: () => {
      subscribers += 1;
      if (subscribers === 1 && !running()) void refresh();
      return () => {
        subscribers -= 1;
        // Only idle reads belong to the screen. Downloads and their status polling
        // continue until completion or an explicit cancellation.
        if (subscribers === 0 && !running()) {
          stopReading();
          store.setState({ ...INITIAL });
        }
      };
    },
    dispose: () => {
      disposed = true;
      stopReading();
      actionController?.abort();
    },
  };
}

const tasks = new Map<string, ReturnType<typeof createSpeechModelTask>>();

export function getSpeechModelTask(
  backend: SpeechModelBackend,
  model: WhisperModelAlias | null,
  language: SpeechModelLanguage | null,
) {
  const key = `${backend}:${model ?? ""}:${language ?? ""}`;
  let task = tasks.get(key);
  if (!task) {
    task = createSpeechModelTask(backend, model, language);
    tasks.set(key, task);
  }
  return task;
}

export function resetSpeechModelTasks() {
  for (const task of tasks.values()) task.dispose();
  tasks.clear();
}
