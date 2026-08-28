import { useEffect, useRef, useState } from "react";
import {
  Activity,
  ChevronDown,
  Clock3,
  Headphones,
  Mic2,
  PanelRightOpen,
  Radio,
  Square,
  X,
} from "lucide-react";
import type { DeviceId, SendFn, SocketState } from "../../types";
import { setAssistantWindowVisible } from "../../platform/tauriWindow";
import { levelToPercent } from "../../utils/audioLevel";
import { Button, InlineNotice } from "../ui";

interface Props {
  state: SocketState;
  send: SendFn;
}

export function MeetingControls({ state, send }: Props) {
  const [seconds, setSeconds] = useState<number | null>(() =>
    elapsedSeconds(state.session?.startedAt),
  );
  const [confirmingStop, setConfirmingStop] = useState(false);
  const continueButtonRef = useRef<HTMLButtonElement>(null);
  const [audioExpanded, setAudioExpanded] = useState(false);
  const audioHealthy = state.connected && state.isRunning;

  useEffect(() => {
    if (confirmingStop) continueButtonRef.current?.focus();
  }, [confirmingStop]);

  useEffect(() => {
    setSeconds(elapsedSeconds(state.session?.startedAt));
    const timerId = window.setInterval(() => {
      setSeconds(elapsedSeconds(state.session?.startedAt));
    }, 1000);
    return () => window.clearInterval(timerId);
  }, [state.session?.startedAt]);

  function stopMeeting() {
    send({ type: "stop_meeting" });
    setConfirmingStop(false);
  }

  return (
    <header className="shrink-0 rounded-2xl border border-line bg-surface px-3 py-2.5 shadow-sm">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-positive-soft text-positive">
            <Radio
              aria-hidden="true"
              size={17}
              className="animate-pulse motion-reduce:animate-none"
            />
            <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-positive" />
          </span>
          <div className="min-w-0">
            <h1
              id="meeting-control-heading"
              className="font-display text-sm font-bold text-ink"
            >
              会話ワークスペース
            </h1>
            <p className="truncate text-xs text-ink-muted max-[800px]:hidden">
              {state.session?.title || "進行中の会議"}
            </p>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-2 rounded-xl bg-paper px-3 py-1.5">
          <Clock3 aria-hidden="true" size={14} className="text-ink-muted" />
          <span
            className="font-mono text-base font-semibold tabular-nums tracking-tight text-ink"
            aria-label={`経過時間 ${formatElapsed(seconds)}`}
          >
            {formatElapsed(seconds)}
          </span>
        </div>

        <Button
          variant="quiet"
          size="sm"
          aria-expanded={audioExpanded || !audioHealthy}
          aria-controls="meeting-audio-details"
          onClick={() => setAudioExpanded((expanded) => !expanded)}
          className={
            audioHealthy
              ? "gap-1.5 text-positive hover:bg-positive-soft"
              : "gap-1.5 bg-warning-soft text-warning hover:bg-warning-soft"
          }
        >
          <Activity aria-hidden="true" size={14} />
          音声 {audioHealthy ? "正常" : "要確認"}
          <ChevronDown
            aria-hidden="true"
            size={13}
            className={`transition-transform motion-reduce:transition-none ${
              audioExpanded || !audioHealthy ? "rotate-180" : ""
            }`}
          />
        </Button>

        <Button
          variant="primary"
          size="sm"
          onClick={() => void setAssistantWindowVisible(true)}
          aria-label="プロンプターに表示"
          className="shadow-sm"
        >
          <PanelRightOpen aria-hidden="true" size={15} />
          <span className="max-[800px]:hidden">プロンプターに表示</span>
          <span className="hidden max-[800px]:inline">プロンプター</span>
        </Button>

        {!confirmingStop && (
          <Button
            variant="quiet"
            size="sm"
            onClick={() => setConfirmingStop(true)}
            aria-label="会議を終了"
            className="text-ink-muted hover:bg-danger-soft hover:text-danger max-[800px]:w-9 max-[800px]:px-0"
          >
            <Square aria-hidden="true" size={12} />
            <span className="max-[800px]:sr-only">終了</span>
          </Button>
        )}
      </div>

      {(audioExpanded || !audioHealthy) && (
        <div
          id="meeting-audio-details"
          className={`mt-2 grid grid-cols-2 gap-4 rounded-xl border px-3 py-2.5 ${
            audioHealthy
              ? "border-line bg-paper/70"
              : "border-warning/25 bg-warning-soft"
          }`}
        >
          <AudioStatus
            label="相手側の音声"
            level={state.levelOther}
            deviceName={deviceNameFor(state, state.deviceOther, true)}
            color="bg-cue"
            icon={Headphones}
          />
          <AudioStatus
            label="自分のマイク"
            level={state.levelSelf}
            deviceName={deviceNameFor(state, state.deviceSelf, false)}
            color="bg-positive"
            icon={Mic2}
          />
        </div>
      )}

      {confirmingStop && (
        <InlineNotice
          tone="danger"
          title="この会議を終了しますか？"
          className="mt-2 rounded-xl px-3 py-2"
          action={
            <div className="flex gap-2">
              <Button
                ref={continueButtonRef}
                variant="secondary"
                size="sm"
                onClick={() => setConfirmingStop(false)}
              >
                <X aria-hidden="true" size={14} />
                続ける
              </Button>
              <Button variant="danger" size="sm" onClick={stopMeeting}>
                <Square aria-hidden="true" size={11} fill="currentColor" />
                終了する
              </Button>
            </div>
          }
        >
          音声の取り込みとライブ支援を停止します。
        </InlineNotice>
      )}
    </header>
  );
}

function elapsedSeconds(startedAt: string | undefined): number | null {
  if (!startedAt) return null;
  const startedAtMs = Date.parse(startedAt);
  if (!Number.isFinite(startedAtMs)) return null;
  return Math.max(0, Math.floor((Date.now() - startedAtMs) / 1000));
}

function formatElapsed(seconds: number | null): string {
  if (seconds === null) return "--:--";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = seconds % 60;
  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
}

function deviceNameFor(
  state: SocketState,
  deviceId: DeviceId,
  prefersMonitor: boolean,
): string {
  const defaultLabel = prefersMonitor ? "既定スピーカー" : "既定マイク";
  if (deviceId === null) {
    const defaultDevice =
      state.devices.find(
        (device) => device.is_default && device.is_monitor === prefersMonitor,
      ) ??
      state.devices.find(
        (device) => device.is_default && device.is_monitor !== prefersMonitor,
      );
    return defaultDevice
      ? `${defaultLabel}（${defaultDevice.name}）`
      : defaultLabel;
  }
  return (
    state.devices.find((device) => String(device.index) === String(deviceId))
      ?.name ?? "状態不明"
  );
}

type AudioIcon = typeof Mic2;

interface AudioStatusProps {
  label: string;
  level: number;
  deviceName: string;
  color: string;
  icon: AudioIcon;
}

function AudioStatus({
  label,
  level,
  deviceName,
  color,
  icon: Icon,
}: AudioStatusProps) {
  const percent = Math.round(levelToPercent(level));
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <Icon aria-hidden="true" size={15} className="text-ink-muted" />
        <span className="text-sm font-semibold text-ink">{label}</span>
        <span
          className="ml-auto max-w-[45%] truncate text-xs text-ink-muted"
          title={deviceName}
        >
          {deviceName}
        </span>
      </div>
      <div
        className="h-2 overflow-hidden rounded-full bg-line"
        role="meter"
        aria-label={`${label}の入力レベル`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
      >
        <div
          className={`h-full rounded-full ${color} transition-[width] duration-75 motion-reduce:transition-none`}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}
