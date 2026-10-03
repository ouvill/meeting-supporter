import { useEffect, useRef, useState } from "react";
import type { SendFn, SocketState, WsMessage } from "../types";

type StartCommand = Extract<WsMessage, { type: "start_meeting" }>;
type Phase = "idle" | "preparing" | "starting" | "cancelling";

/** The user's single start action owns preparation and its continuation. */
export function useMeetingStart(state: SocketState, send: SendFn) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<{
    command: StartCommand;
    phase: Phase;
    errorRevision: number;
  } | null>(null);
  const cancellation = useRef<{
    stateRevision: number;
    errorRevision: number;
  } | null>(null);

  useEffect(() => {
    if (cancellation.current) {
      const failed =
        (state.errorRevision ?? 0) !== cancellation.current.errorRevision;
      const stopped =
        !state.sttInitialized &&
        !state.sttInitializing &&
        (state.sttStateRevision ?? 0) !== cancellation.current.stateRevision;
      if (!state.connected || failed || stopped) {
        cancellation.current = null;
        setPhase("idle");
        if (failed)
          setError(
            "準備の取り消しに失敗しました。音声入力の状態を確認してください。",
          );
      }
      return;
    }
    const request = pending.current;
    if (!request) return;
    if (
      !state.connected ||
      (state.errorRevision ?? 0) !== request.errorRevision
    ) {
      pending.current = null;
      setPhase("idle");
      setError(
        !state.connected
          ? "接続が切れました。接続が戻ってから、もう一度開始してください。"
          : "会議を開始できませんでした。音声入力と音声認識の設定を確認して、もう一度お試しください。",
      );
    } else if (request.phase === "preparing" && state.sttInitialized) {
      request.phase = "starting";
      setPhase("starting");
      send(request.command);
    } else if (state.isRunning) {
      pending.current = null;
      setPhase("idle");
    }
  }, [
    state.connected,
    state.errorRevision,
    state.sttInitialized,
    state.sttInitializing,
    state.sttStateRevision,
    state.isRunning,
    send,
  ]);

  useEffect(
    () => () => {
      if (pending.current?.phase === "preparing")
        send({ type: "shutdown_stt" });
      pending.current = null;
    },
    [send],
  );

  const start = (command: StartCommand) => {
    if (
      pending.current ||
      cancellation.current ||
      !state.connected ||
      state.isRunning ||
      state.meetingEndStatus === "stop_failed"
    )
      return;
    const needsPreparation =
      ["local", "whisper", "reazonspeech"].includes(state.sttBackend) &&
      !state.sttInitialized;
    const nextPhase = needsPreparation ? "preparing" : "starting";
    pending.current = {
      command,
      phase: nextPhase,
      errorRevision: state.errorRevision ?? 0,
    };
    setError(null);
    setPhase(nextPhase);
    if (needsPreparation) {
      if (!state.sttInitializing && !state.sttInitRequested)
        send({ type: "init_stt" });
    } else send(command);
  };
  const cancel = () => {
    if (pending.current?.phase !== "preparing") return;
    pending.current = null;
    cancellation.current = {
      stateRevision: state.sttStateRevision ?? 0,
      errorRevision: state.errorRevision ?? 0,
    };
    setPhase("cancelling");
    setError(null);
    send({ type: "shutdown_stt" });
  };
  return { phase, error, start, cancel };
}
