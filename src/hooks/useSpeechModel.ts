import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelSpeechModelDownloadApiSttModelCancelPost,
  getSpeechModelStatusApiSttModelGet,
  startSpeechModelDownloadApiSttModelDownloadPost,
} from "../api/generated/sdk.gen";
import type { SpeechModelStatusResponse } from "../api/generated/types.gen";

export type SpeechModelLanguage = "ja" | "en";
export type SpeechModelBackend = "vosk" | "whisper";
export type WhisperModelAlias =
  | "tiny"
  | "base"
  | "small"
  | "medium"
  | "large-v2"
  | "large-v3-turbo";
export type SpeechModelAction = "starting" | "cancelling" | null;

export type SpeechModelStatus = SpeechModelStatusResponse;

interface ProviderAwareRequest {
  backend: SpeechModelBackend;
  language: SpeechModelLanguage;
  model?: WhisperModelAlias;
}

const getSpeechModelStatus = getSpeechModelStatusApiSttModelGet;
const startSpeechModelDownload =
  startSpeechModelDownloadApiSttModelDownloadPost;
const cancelSpeechModelDownload =
  cancelSpeechModelDownloadApiSttModelCancelPost;

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

function sameSelection(
  status: SpeechModelStatus,
  backend: SpeechModelBackend,
  model: WhisperModelAlias | null,
  language: SpeechModelLanguage,
): boolean {
  return (
    status.language === language &&
    status.backend === backend &&
    (model === null || status.model_id === model)
  );
}

export function useSpeechModel(
  backend: SpeechModelBackend,
  model: WhisperModelAlias | null,
  language: SpeechModelLanguage | null,
  enabled = true,
): SpeechModelController {
  const selectedKey = `${backend}:${model ?? ""}:${language ?? ""}`;

  const [status, setStatus] = useState<SpeechModelStatus | null>(null);
  const [action, setAction] = useState<SpeechModelAction>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmingStart, setConfirmingStart] = useState(false);
  const [settledSelectionKey, setSettledSelectionKey] = useState<string | null>(
    null,
  );

  const mountedRef = useRef(false);
  const statusRef = useRef<SpeechModelStatus | null>(null);
  const actionRef = useRef<SpeechModelAction>(null);
  const actionInFlightRef = useRef(false);
  const confirmingStartRef = useRef(false);
  const generationRef = useRef(0);
  const requestSequenceRef = useRef(0);
  const actionTokenRef = useRef(0);
  const controllersRef = useRef(new Set<AbortController>());

  const request = useMemo<ProviderAwareRequest | null>(
    () =>
      language === null
        ? null
        : {
            backend,
            language,
            ...(model ? { model } : {}),
          },
    [backend, language, model],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      for (const controller of controllersRef.current) controller.abort();
      controllersRef.current.clear();
    };
  }, []);

  useEffect(() => {
    const generation = ++generationRef.current;
    actionTokenRef.current += 1;
    actionInFlightRef.current = false;
    actionRef.current = null;
    confirmingStartRef.current = false;
    statusRef.current = null;
    setStatus(null);
    setAction(null);
    setError(null);
    setSettledSelectionKey(null);
    setConfirmingStart(false);

    return () => {
      if (generationRef.current === generation) generationRef.current += 1;
      actionTokenRef.current += 1;
      actionInFlightRef.current = false;
      actionRef.current = null;
      confirmingStartRef.current = false;
      for (const controller of controllersRef.current) controller.abort();
      controllersRef.current.clear();
    };
  }, [enabled, selectedKey]);

  const refresh = useCallback(async () => {
    if (!enabled || request === null) return;

    const generation = generationRef.current;
    const requestSequence = ++requestSequenceRef.current;
    const controller = new AbortController();
    controllersRef.current.add(controller);
    setError(null);
    setSettledSelectionKey(null);

    try {
      const result = await getSpeechModelStatus({
        query: request,
        signal: controller.signal,
      });
      if (
        controller.signal.aborted ||
        !mountedRef.current ||
        generationRef.current !== generation ||
        requestSequenceRef.current !== requestSequence
      )
        return;

      if (
        result.error ||
        !result.data ||
        !sameSelection(result.data, backend, model, request.language)
      ) {
        setSettledSelectionKey(selectedKey);
        setError(STATUS_ERROR);
        return;
      }

      statusRef.current = result.data;
      setStatus(result.data);
      setSettledSelectionKey(selectedKey);
    } catch {
      if (
        !controller.signal.aborted &&
        mountedRef.current &&
        generationRef.current === generation &&
        requestSequenceRef.current === requestSequence
      ) {
        setSettledSelectionKey(selectedKey);
        setError(STATUS_ERROR);
      }
    } finally {
      controllersRef.current.delete(controller);
    }
  }, [backend, enabled, model, request, selectedKey]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const startDownload = useCallback(async () => {
    if (
      !enabled ||
      request === null ||
      actionInFlightRef.current ||
      confirmingStartRef.current ||
      statusRef.current?.state === "downloading"
    )
      return;

    const generation = generationRef.current;
    const actionToken = ++actionTokenRef.current;
    const requestSequence = ++requestSequenceRef.current;
    const controller = new AbortController();
    controllersRef.current.add(controller);
    actionInFlightRef.current = true;
    actionRef.current = "starting";
    setAction("starting");
    setError(null);

    try {
      const result = await startSpeechModelDownload({
        body: request,
        signal: controller.signal,
      });
      if (
        controller.signal.aborted ||
        !mountedRef.current ||
        generationRef.current !== generation ||
        requestSequenceRef.current !== requestSequence
      )
        return;

      if (
        result.error ||
        !result.data ||
        !sameSelection(result.data, backend, model, request.language)
      ) {
        confirmingStartRef.current = true;
        setConfirmingStart(true);
        setError(START_CONFIRM_ERROR);
        return;
      }

      statusRef.current = result.data;
      setStatus(result.data);
    } catch {
      if (
        !controller.signal.aborted &&
        mountedRef.current &&
        generationRef.current === generation &&
        requestSequenceRef.current === requestSequence
      ) {
        confirmingStartRef.current = true;
        setConfirmingStart(true);
        setError(START_CONFIRM_ERROR);
      }
    } finally {
      controllersRef.current.delete(controller);
      if (
        mountedRef.current &&
        generationRef.current === generation &&
        actionTokenRef.current === actionToken
      ) {
        actionInFlightRef.current = false;
        actionRef.current = null;
        setAction(null);
      }
    }
  }, [backend, enabled, model, request]);

  const cancelDownload = useCallback(async () => {
    if (
      !enabled ||
      request === null ||
      actionInFlightRef.current ||
      statusRef.current?.state !== "downloading" ||
      !statusRef.current.cancelable
    )
      return;

    const generation = generationRef.current;
    const actionToken = ++actionTokenRef.current;
    const requestSequence = ++requestSequenceRef.current;
    const controller = new AbortController();
    controllersRef.current.add(controller);
    actionInFlightRef.current = true;
    actionRef.current = "cancelling";
    setAction("cancelling");
    setError(null);

    try {
      const result = await cancelSpeechModelDownload({
        query: request,
        signal: controller.signal,
      });
      if (
        controller.signal.aborted ||
        !mountedRef.current ||
        generationRef.current !== generation ||
        requestSequenceRef.current !== requestSequence
      )
        return;

      if (
        result.error ||
        !result.data ||
        !sameSelection(result.data, backend, model, request.language)
      ) {
        setError(CANCEL_ERROR);
        return;
      }

      statusRef.current = result.data;
      setStatus(result.data);
    } catch {
      if (
        !controller.signal.aborted &&
        mountedRef.current &&
        generationRef.current === generation &&
        requestSequenceRef.current === requestSequence
      )
        setError(CANCEL_ERROR);
    } finally {
      controllersRef.current.delete(controller);
      if (
        mountedRef.current &&
        generationRef.current === generation &&
        actionTokenRef.current === actionToken
      ) {
        actionInFlightRef.current = false;
        actionRef.current = null;
        setAction(null);
      }
    }
  }, [backend, enabled, model, request]);

  const visibleStatus =
    status !== null &&
    language !== null &&
    sameSelection(status, backend, model, language)
      ? status
      : null;

  useEffect(() => {
    if (!enabled || request === null || !confirmingStart || action !== null)
      return;

    const generation = generationRef.current;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let requestController: AbortController | null = null;

    const reconcile = async () => {
      const requestSequence = ++requestSequenceRef.current;
      const controller = new AbortController();
      requestController = controller;
      controllersRef.current.add(controller);

      try {
        const result = await getSpeechModelStatus({
          query: request,
          signal: controller.signal,
        });
        if (
          !active ||
          controller.signal.aborted ||
          !mountedRef.current ||
          generationRef.current !== generation ||
          requestSequenceRef.current !== requestSequence
        )
          return;

        if (
          result.error ||
          !result.data ||
          !sameSelection(result.data, backend, model, request.language)
        ) {
          setError(START_CONFIRM_ERROR);
          return;
        }

        statusRef.current = result.data;
        setStatus(result.data);
        confirmingStartRef.current = false;
        setConfirmingStart(false);
        setError(
          result.data.state === "downloading" || result.data.state === "ready"
            ? null
            : START_ERROR,
        );
      } catch {
        if (
          active &&
          !controller.signal.aborted &&
          mountedRef.current &&
          generationRef.current === generation &&
          requestSequenceRef.current === requestSequence
        )
          setError(START_CONFIRM_ERROR);
      } finally {
        controllersRef.current.delete(controller);
        requestController = null;
        if (
          active &&
          mountedRef.current &&
          generationRef.current === generation &&
          confirmingStartRef.current
        )
          timer = setTimeout(reconcile, POLL_INTERVAL_MS);
      }
    };

    timer = setTimeout(reconcile, POLL_INTERVAL_MS);
    return () => {
      active = false;
      clearTimeout(timer);
      requestController?.abort();
    };
  }, [action, backend, confirmingStart, enabled, model, request]);

  useEffect(() => {
    if (
      !enabled ||
      request === null ||
      visibleStatus?.state !== "downloading" ||
      action !== null
    )
      return;

    const generation = generationRef.current;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let requestController: AbortController | null = null;

    const poll = async () => {
      const requestSequence = ++requestSequenceRef.current;
      const controller = new AbortController();
      requestController = controller;
      controllersRef.current.add(controller);

      try {
        const result = await getSpeechModelStatus({
          query: request,
          signal: controller.signal,
        });
        if (
          !active ||
          controller.signal.aborted ||
          !mountedRef.current ||
          generationRef.current !== generation ||
          requestSequenceRef.current !== requestSequence
        )
          return;

        if (
          result.error ||
          !result.data ||
          !sameSelection(result.data, backend, model, request.language)
        ) {
          setError(POLL_ERROR);
          return;
        }

        statusRef.current = result.data;
        setStatus(result.data);
        setError(null);
      } catch {
        if (
          active &&
          !controller.signal.aborted &&
          mountedRef.current &&
          generationRef.current === generation &&
          requestSequenceRef.current === requestSequence
        )
          setError(POLL_ERROR);
      } finally {
        controllersRef.current.delete(controller);
        requestController = null;
        if (
          active &&
          mountedRef.current &&
          generationRef.current === generation &&
          statusRef.current?.state === "downloading" &&
          actionRef.current === null
        )
          timer = setTimeout(poll, POLL_INTERVAL_MS);
      }
    };

    timer = setTimeout(poll, POLL_INTERVAL_MS);
    return () => {
      active = false;
      clearTimeout(timer);
      requestController?.abort();
    };
  }, [action, backend, enabled, model, request, visibleStatus?.state]);

  const visibleError =
    visibleStatus !== null || settledSelectionKey === selectedKey
      ? error
      : null;
  const isDownloading = visibleStatus?.state === "downloading";
  const checkingStatus =
    enabled &&
    language !== null &&
    visibleStatus === null &&
    settledSelectionKey !== selectedKey;
  const blocksSettingsSave =
    checkingStatus || isDownloading || action !== null || confirmingStart;

  return {
    backend,
    model,
    language,
    status: visibleStatus,
    loading: checkingStatus,
    action,
    error: visibleError,
    confirmingStart,
    checkingStatus,
    isDownloading,
    blocksSettingsSave,
    refresh,
    startDownload,
    cancelDownload,
  };
}
