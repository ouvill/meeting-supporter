import { useCallback, useEffect, useRef, useState } from "react";
import { client } from "../../api/generated/client.gen";
import type { RecordingAssetItem } from "../../api/generated/types.gen";
import {
  AlertCircle,
  Headphones,
  Pause,
  Play,
  Volume2,
  VolumeX,
} from "lucide-react";
import { Tooltip } from "../ui/Tooltip";

interface Props {
  meetingId: string;
  recordings: RecordingAssetItem[];
}

// ── Helpers ──────────────────────────────────────────────────────

function getRecordingUrl(meetingId: string, role: "other" | "self"): string {
  const config = client.getConfig();
  const baseUrl = config.baseUrl ?? "";
  const base = baseUrl.replace(/\/$/, "");
  return `${base}/meetings/${meetingId}/recordings/${role}`;
}

function getClientHeaders(): Headers {
  const headers = new Headers();
  const configured = client.getConfig().headers;
  if (!configured) return headers;
  new Headers(configured as HeadersInit).forEach((value, key) => {
    headers.set(key, value);
  });
  return headers;
}

async function fetchRecordingObjectUrl(
  meetingId: string,
  role: "other" | "self",
  signal: AbortSignal,
): Promise<string> {
  const response = await fetch(getRecordingUrl(meetingId, role), {
    headers: getClientHeaders(),
    signal,
  });
  if (!response.ok) {
    throw new Error(`Recording fetch failed: ${response.status}`);
  }
  return URL.createObjectURL(await response.blob());
}

/** Clamp to a safe finite duration value; returns 0 for NaN/Infinity/negative. */
function safeDuration(d: number | undefined): number {
  if (d == null) return 0;
  return Number.isFinite(d) && d > 0 ? d : 0;
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/**
 * Whether the audio element can play at a given playback position.
 * Returns false if the element is absent or its finite duration is exceeded.
 */
function canPlayAt(el: HTMLAudioElement | null, time: number): boolean {
  if (!el) return false;
  const dur = el.duration;
  // Duration unknown (not loaded yet / live) — attempt playback
  if (!Number.isFinite(dur) || dur === 0) return true;
  // Finite duration — only play if position is strictly before the end
  return time < dur;
}

// ── Component ────────────────────────────────────────────────────

export function RecordingPlayer({ meetingId, recordings }: Props) {
  const otherAsset = recordings.find((r) => r.role === "other") ?? null;
  const selfAsset = recordings.find((r) => r.role === "self") ?? null;
  const hasAnyRecording = !!otherAsset || !!selfAsset;

  const otherRef = useRef<HTMLAudioElement | null>(null);
  const selfRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [volumeOther, setVolumeOther] = useState(1);
  const [mutedOther, setMutedOther] = useState(false);
  const [volumeSelf, setVolumeSelf] = useState(1);
  const [mutedSelf, setMutedSelf] = useState(false);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const [audioUrls, setAudioUrls] = useState<{
    other: string | null;
    self: string | null;
  }>({
    other: null,
    self: null,
  });
  const animRef = useRef<number | null>(null);

  // ── Sync helper ──────────────────────────────────────────────

  const getActiveDuration = useCallback(() => {
    const other = otherRef.current;
    const self = selfRef.current;
    return Math.max(
      safeDuration(other?.duration),
      safeDuration(self?.duration),
    );
  }, []);

  const updateTime = useCallback(() => {
    const other = otherRef.current;
    const self = selfRef.current;
    const t = Math.max(other?.currentTime ?? 0, self?.currentTime ?? 0);
    setCurrentTime(t);
    if (animRef.current) {
      animRef.current = requestAnimationFrame(updateTime);
    }
  }, []);

  // ── Controls ─────────────────────────────────────────────────

  const togglePlay = useCallback(() => {
    const other = otherRef.current;
    const self = selfRef.current;
    if (playing) {
      other?.pause();
      self?.pause();
      if (animRef.current) cancelAnimationFrame(animRef.current);
      setPlaying(false);
      return;
    }

    // Clear any previous error on retry
    setPlaybackError(null);

    // Only play tracks that can still play at the current position
    const playable = [other, self].filter((el): el is HTMLAudioElement =>
      canPlayAt(el, currentTime),
    );

    if (playable.length === 0) {
      setPlaybackError("再生可能なトラックがありません。");
      return;
    }

    // Attempt playback on all eligible tracks
    const settled = Promise.allSettled(playable.map((el) => el.play()));

    // Wait for at least one to succeed before setting playing=true
    settled.then((results) => {
      const anySuccess = results.some((r) => r.status === "fulfilled");
      if (anySuccess) {
        setPlaying(true);
        animRef.current = requestAnimationFrame(updateTime);
      } else {
        setPlaybackError(
          "録音ファイルの再生に失敗しました。ファイルが見つからないか、読み込みエラーが発生しました。",
        );
      }
    });
  }, [playing, updateTime, currentTime, otherAsset, selfAsset]);

  const seek = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setPlaybackError(null);
    const t = Number(e.target.value);
    const other = otherRef.current;
    const self = selfRef.current;
    if (other) other.currentTime = t;
    if (self) self.currentTime = t;
    setCurrentTime(t);
  }, []);

  const changeRate = useCallback((rate: number) => {
    setPlaybackError(null);
    const other = otherRef.current;
    const self = selfRef.current;
    if (other) other.playbackRate = rate;
    if (self) self.playbackRate = rate;
    setPlaybackRate(rate);
  }, []);

  // ── Volume / mute handlers ───────────────────────────────────

  const handleVolumeOther = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const v = Number(e.target.value);
      setVolumeOther(v);
      if (otherRef.current) otherRef.current.volume = v;
    },
    [],
  );

  const handleMuteOther = useCallback(() => {
    setMutedOther((prev) => {
      const next = !prev;
      if (otherRef.current) otherRef.current.muted = next;
      return next;
    });
  }, []);

  const handleVolumeSelf = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const v = Number(e.target.value);
      setVolumeSelf(v);
      if (selfRef.current) selfRef.current.volume = v;
    },
    [],
  );

  const handleMuteSelf = useCallback(() => {
    setMutedSelf((prev) => {
      const next = !prev;
      if (selfRef.current) selfRef.current.muted = next;
      return next;
    });
  }, []);

  // ── Duration from loadedmetadata ─────────────────────────────

  const onMeta = useCallback(() => {
    setDuration(getActiveDuration());
  }, [getActiveDuration]);

  useEffect(() => {
    const createdUrls: string[] = [];
    const abortController = new AbortController();
    let cancelled = false;

    async function loadRole(role: "other" | "self"): Promise<string | null> {
      const url = await fetchRecordingObjectUrl(
        meetingId,
        role,
        abortController.signal,
      );
      if (cancelled) {
        URL.revokeObjectURL(url);
        return null;
      }
      createdUrls.push(url);
      return url;
    }

    async function loadRecordings(): Promise<void> {
      const next = {
        other: null as string | null,
        self: null as string | null,
      };
      try {
        if (otherAsset) next.other = await loadRole("other");
        if (selfAsset) next.self = await loadRole("self");
      } catch (error) {
        const aborted =
          error instanceof DOMException && error.name === "AbortError";
        if (!cancelled && !aborted) {
          setPlaybackError("録音ファイルの読み込みに失敗しました。");
        }
      }
      if (!cancelled) {
        setAudioUrls(next);
      }
    }

    void loadRecordings();

    return () => {
      cancelled = true;
      abortController.abort();
      if (animRef.current) cancelAnimationFrame(animRef.current);
      for (const url of createdUrls) URL.revokeObjectURL(url);
    };
  }, [meetingId, otherAsset, selfAsset]);

  // ── Render ───────────────────────────────────────────────────

  if (!hasAnyRecording) {
    return (
      <div className="rounded-xl border border-dashed border-line-strong bg-surface px-5 py-8 text-center">
        <Headphones
          aria-hidden="true"
          className="mx-auto size-5 text-ink-faint"
        />
        <p className="mt-3 text-sm font-semibold text-ink">
          録音ファイルはありません
        </p>
        <p className="mt-1 text-xs text-ink-muted">
          この会議では録音が保存されていません。
        </p>
      </div>
    );
  }

  const rates = [0.5, 0.75, 1, 1.25, 1.5, 2];

  return (
    <div className="min-w-0 space-y-4 rounded-2xl border border-line bg-surface p-4 shadow-card sm:p-5">
      {otherAsset && (
        <audio
          ref={otherRef}
          src={audioUrls.other ?? undefined}
          preload="auto"
          onLoadedMetadata={onMeta}
          onEnded={() => {
            const other = otherRef.current;
            const self = selfRef.current;
            if ((!other || other.ended) && (!self || self.ended)) {
              setPlaying(false);
              if (animRef.current) cancelAnimationFrame(animRef.current);
            }
          }}
        />
      )}
      {selfAsset && (
        <audio
          ref={selfRef}
          src={audioUrls.self ?? undefined}
          preload="auto"
          onLoadedMetadata={onMeta}
          onEnded={() => {
            const other = otherRef.current;
            const self = selfRef.current;
            if ((!other || other.ended) && (!self || self.ended)) {
              setPlaying(false);
              if (animRef.current) cancelAnimationFrame(animRef.current);
            }
          }}
        />
      )}

      {playbackError && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-xl border border-danger/25 bg-danger-soft px-3 py-2.5 text-xs leading-5 text-danger"
        >
          <AlertCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span>{playbackError}</span>
        </div>
      )}

      <div className="grid min-w-0 grid-cols-[2.75rem_minmax(0,1fr)_auto] items-center gap-3">
        <Tooltip content={playing ? "一時停止" : "再生"}>
          <button
            type="button"
            onClick={togglePlay}
            className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-white transition-colors hover:bg-primary-hover"
            aria-label={playing ? "一時停止" : "再生"}
          >
            {playing ? (
              <Pause aria-hidden="true" className="size-4 fill-current" />
            ) : (
              <Play aria-hidden="true" className="ml-0.5 size-4 fill-current" />
            )}
          </button>
        </Tooltip>

        <div className="min-w-0">
          <div className="mb-1.5 flex items-center justify-between gap-3 text-xs tabular-nums text-ink-muted">
            <span>{formatTime(currentTime)}</span>
            <span>{formatTime(duration)}</span>
          </div>
          <input
            type="range"
            min={0}
            max={safeDuration(duration) || 1}
            step={0.1}
            value={currentTime}
            onChange={seek}
            className="block h-1.5 w-full min-w-0 cursor-pointer accent-primary"
            aria-label="シーク"
            aria-valuetext={`${formatTime(currentTime)} / ${formatTime(duration)}`}
          />
          <span className="sr-only">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>
        </div>

        <select
          value={playbackRate}
          onChange={(event) => changeRate(Number(event.target.value))}
          className="min-h-9 rounded-lg border border-line bg-surface px-2 py-1 text-xs font-semibold text-ink-muted"
          aria-label="再生速度"
        >
          {rates.map((rate) => (
            <option key={rate} value={rate}>
              {rate}x
            </option>
          ))}
        </select>
      </div>

      <div className="grid min-w-0 grid-cols-1 gap-3 border-t border-line pt-4 min-[560px]:grid-cols-2 min-[560px]:gap-5">
        <VolumeSlider
          label="相手の音声"
          dotColor="bg-cue"
          volume={volumeOther}
          muted={mutedOther}
          onVolume={handleVolumeOther}
          onMute={handleMuteOther}
          disabled={!otherAsset}
        />
        <VolumeSlider
          label="自分の音声"
          dotColor="bg-positive"
          volume={volumeSelf}
          muted={mutedSelf}
          onVolume={handleVolumeSelf}
          onMute={handleMuteSelf}
          disabled={!selfAsset}
        />
      </div>
    </div>
  );
}

// ── VolumeSlider sub-component ───────────────────────────────────

interface VolumeSliderProps {
  label: string;
  dotColor: string;
  volume: number;
  muted: boolean;
  onVolume: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onMute: () => void;
  disabled: boolean;
}

function VolumeSlider({
  label,
  dotColor,
  volume,
  muted,
  onVolume,
  onMute,
  disabled,
}: VolumeSliderProps) {
  const muteButton = (
    <button
      type="button"
      onClick={onMute}
      disabled={disabled}
      className="flex size-8 items-center justify-center rounded-full text-ink-muted transition-colors hover:bg-primary-soft hover:text-primary disabled:opacity-40"
      aria-label={muted ? "ミュート解除" : "ミュート"}
      aria-pressed={muted}
    >
      {muted ? (
        <VolumeX aria-hidden="true" className="size-4" />
      ) : (
        <Volume2 aria-hidden="true" className="size-4" />
      )}
    </button>
  );

  return (
    <div
      className={`flex min-w-0 items-center gap-2 ${disabled ? "opacity-40" : ""}`}
    >
      <span
        className={`size-2 shrink-0 rounded-full ${dotColor}`}
        aria-hidden="true"
      />
      <span className="w-16 shrink-0 text-xs font-semibold text-ink-muted">
        {label}
      </span>
      {disabled ? (
        <Tooltip content={`${label}の録音はありません`}>
          <span
            className="inline-flex shrink-0"
            tabIndex={0}
            aria-label={`${label}の録音はありません`}
          >
            {muteButton}
          </span>
        </Tooltip>
      ) : (
        <Tooltip
          content={muted ? `${label}のミュートを解除` : `${label}をミュート`}
        >
          {muteButton}
        </Tooltip>
      )}
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={volume}
        onChange={onVolume}
        disabled={disabled}
        className="h-1 min-w-0 flex-1 cursor-pointer accent-primary"
        aria-label={`${label} 音量`}
        aria-valuenow={muted ? 0 : volume}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuetext={muted ? "ミュート中" : `${Math.round(volume * 100)}%`}
      />
    </div>
  );
}
