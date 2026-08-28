import { useEffect, useRef, useState } from "react";
import { Volume2 } from "lucide-react";
import { Button } from "../ui";

type SystemAudioTestStatus = "idle" | "playing" | "played" | "error";

interface SystemAudioPlayback {
  context: AudioContext;
  oscillator: OscillatorNode;
}

export function SystemAudioTestControl() {
  const playbackRef = useRef<SystemAudioPlayback | null>(null);
  const [status, setStatus] = useState<SystemAudioTestStatus>("idle");

  useEffect(
    () => () => {
      const playback = playbackRef.current;
      playbackRef.current = null;
      if (!playback) return;
      playback.oscillator.onended = null;
      try {
        playback.oscillator.stop();
      } catch {
        // The oscillator may already have ended.
      }
      void playback.context.close().catch(() => undefined);
    },
    [],
  );

  async function playTestSound() {
    if (playbackRef.current) return;
    setStatus("playing");

    let context: AudioContext | null = null;
    try {
      if (typeof window.AudioContext !== "function") {
        setStatus("error");
        return;
      }

      const audioContext = new window.AudioContext();
      context = audioContext;
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      playbackRef.current = { context: audioContext, oscillator };

      oscillator.type = "sine";
      oscillator.connect(gain);
      gain.connect(audioContext.destination);
      await audioContext.resume();

      const startAt = audioContext.currentTime + 0.02;
      const stopAt = startAt + 0.7;
      oscillator.frequency.setValueAtTime(523.25, startAt);
      oscillator.frequency.linearRampToValueAtTime(659.25, startAt + 0.35);
      gain.gain.setValueAtTime(0, startAt);
      gain.gain.linearRampToValueAtTime(0.14, startAt + 0.04);
      gain.gain.setValueAtTime(0.14, stopAt - 0.1);
      gain.gain.linearRampToValueAtTime(0, stopAt);

      oscillator.onended = () => {
        if (playbackRef.current?.context !== audioContext) return;
        playbackRef.current = null;
        void audioContext.close().catch(() => undefined);
        setStatus("played");
      };
      oscillator.start(startAt);
      oscillator.stop(stopAt);
    } catch {
      playbackRef.current = null;
      if (context) void context.close().catch(() => undefined);
      setStatus("error");
    }
  }

  return (
    <div className="-mt-1 rounded-xl border border-line bg-surface px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-ink">相手側の音声をテスト</p>
          <p className="mt-0.5 text-xs leading-5 text-ink-muted">
            この端末の既定の出力から短い音を流します。
          </p>
        </div>
        <Button
          variant="quiet"
          size="sm"
          onClick={() => void playTestSound()}
          disabled={status === "playing"}
          className="shrink-0"
        >
          <Volume2 aria-hidden="true" className="size-3.5" />
          {status === "playing" ? "再生中…" : "テスト音を再生"}
        </Button>
      </div>
      {status === "played" && (
        <p
          role="status"
          aria-live="polite"
          className="mt-1 text-xs leading-5 text-positive"
        >
          相手側の音量バーが動いたか確認してください。
        </p>
      )}
      {status === "error" && (
        <p
          role="status"
          aria-live="polite"
          className="mt-1 text-xs leading-5 text-danger"
        >
          テスト音を再生できませんでした。端末の音量設定を確認してください。
        </p>
      )}
    </div>
  );
}

interface AudioPreparationProps {
  initialized: boolean;
  initializing: boolean;
  initRequested: boolean;
  failed: boolean;
  onInit: () => void;
  onShutdown: () => void;
}

export function AudioPreparation({
  initialized,
  initializing,
  initRequested,
  failed,
  onInit,
  onShutdown,
}: AudioPreparationProps) {
  const preparing = initializing || initRequested;

  if (initialized) {
    return (
      <div
        className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-positive/20 bg-positive-soft px-3 py-2.5"
        aria-live="polite"
      >
        <p className="text-xs font-semibold text-positive">
          音声認識を使えます
        </p>
        <button
          type="button"
          onClick={onShutdown}
          className="rounded-lg px-2 py-1 text-xs font-medium text-ink-muted hover:bg-surface hover:text-danger"
        >
          やり直す
        </button>
      </div>
    );
  }

  if (preparing) {
    return (
      <div
        className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-warning/20 bg-warning-soft px-3 py-2.5"
        aria-live="polite"
      >
        <span className="flex items-center gap-2 text-xs font-semibold text-warning">
          <span
            className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-warning border-t-transparent motion-reduce:animate-none"
            aria-hidden="true"
          />
          音声認識を準備しています…
        </span>
        <button
          type="button"
          onClick={onShutdown}
          className="rounded-lg px-2 py-1 text-xs font-medium text-ink-muted hover:bg-surface hover:text-danger"
        >
          キャンセル
        </button>
      </div>
    );
  }

  return (
    <div className="mt-3 space-y-2" aria-live="polite">
      {failed && (
        <p className="rounded-xl border border-danger/20 bg-danger-soft px-3 py-2 text-xs font-medium text-danger">
          音声認識を準備できませんでした。もう一度お試しください。
        </p>
      )}
      <p className="text-xs leading-5 text-ink-muted">
        初回は必要なデータの読み込みに時間がかかる場合があります。
      </p>
      <button
        type="button"
        onClick={onInit}
        className="w-full rounded-xl border border-line-strong bg-surface px-3 py-2.5 text-xs font-bold text-ink transition-colors hover:border-primary/45 hover:bg-primary-soft hover:text-primary motion-reduce:transition-none"
      >
        音声認識を使えるようにする
      </button>
    </div>
  );
}
