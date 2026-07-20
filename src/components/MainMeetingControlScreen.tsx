import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  Activity,
  BrainCircuit,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clipboard,
  Clock3,
  Headphones,
  ListTodo,
  Mic2,
  PanelRightOpen,
  Radio,
  RefreshCw,
  Sparkles,
  Square,
  X,
} from "lucide-react";
import type {
  DeviceId,
  SendFn,
  SocketState,
  SuggestionCard,
  Turn,
} from "../types";
import { setAssistantWindowVisible } from "../platform/tauriWindow";
import { levelToPercent } from "../utils/audioLevel";
import {
  EmbeddedLiveReplyPanel,
  type ReplyReadiness,
} from "./assistant/LiveReplySidePanel";
import { Button, InlineNotice, Tooltip } from "./ui";
import type { AiUseCaseRouteStatus } from "../hooks/useAiRoutes";

interface Props {
  state: SocketState;
  send: SendFn;
  onSettings: () => void;
  replyReadiness?: ReplyReadiness;
  infoRouteStatus?: AiUseCaseRouteStatus;
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

export function MainMeetingControlScreen({
  state,
  send,
  onSettings,
  replyReadiness,
  infoRouteStatus = {
    readiness: "setup_required",
    canGenerate: false,
    message: "会話メモを利用する支援方法を設定してください。",
  },
}: Props) {
  const [seconds, setSeconds] = useState<number | null>(() =>
    elapsedSeconds(state.session?.startedAt),
  );
  const [confirmingStop, setConfirmingStop] = useState(false);
  const continueButtonRef = useRef<HTMLButtonElement>(null);
  const historyScrollRef = useRef<HTMLDivElement>(null);
  const shouldFollowHistoryRef = useRef(true);
  const [pinnedTurnId, setPinnedTurnId] = useState<string | null>(null);
  const [audioExpanded, setAudioExpanded] = useState(false);
  const audioHealthy = state.connected && state.isRunning;
  const turns = state.session?.turns ?? [];
  const finalTurnCount = turns.length;
  const infoRouteLoading =
    infoRouteStatus.readiness === "unknown" &&
    infoRouteStatus.message === null;
  const canRunInfo =
    state.connected &&
    finalTurnCount > 0 &&
    state.agentSettings.infoEnabled &&
    infoRouteStatus.canGenerate &&
    !state.isResearchingInfo;
  const infoUnavailableMessage = !state.agentSettings.infoEnabled
    ? "設定ファイルで会話メモAIが無効になっています。"
    : infoRouteLoading
      ? "会話メモの支援方法を確認しています。"
      : !infoRouteStatus.canGenerate
        ? (infoRouteStatus.message ??
          "会話メモの支援方法を準備してから利用できます。")
        : null;
  const showInfoSettings =
    state.agentSettings.infoEnabled &&
    !infoRouteLoading &&
    !infoRouteStatus.canGenerate;

  function runInfo() {
    if (!canRunInfo) return;
    send({ type: "run_info" });
  }

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

  useLayoutEffect(() => {
    if (!shouldFollowHistoryRef.current) return;
    const panel = historyScrollRef.current;
    if (panel) panel.scrollTop = panel.scrollHeight;
  }, [state.session?.turns, state.interimOther, state.interimSelf]);

  function stopMeeting() {
    send({ type: "stop_meeting" });
    setConfirmingStop(false);
  }

  function updateHistoryFollowState() {
    const panel = historyScrollRef.current;
    if (!panel) return;
    shouldFollowHistoryRef.current =
      panel.scrollHeight - panel.scrollTop - panel.clientHeight <= 24;
  }

  return (
    <main
      data-testid="meeting-control-screen"
      className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-paper px-4 pb-4 pt-3 text-ink sm:px-5"
      aria-labelledby="meeting-control-heading"
    >
      <div className="mx-auto flex min-h-0 w-full max-w-[1240px] flex-1 flex-col gap-3">
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

        <div className="grid min-h-0 flex-1 grid-cols-[minmax(250px,0.85fr)_minmax(360px,1.15fr)] gap-3 max-[680px]:grid-cols-1 max-[680px]:overflow-y-auto">
          <section
            className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-sm"
            aria-labelledby="conversation-history-heading"
          >
            <div className="shrink-0 border-b border-line px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-[10px] font-bold tracking-[0.16em] text-positive">
                    LIVE TRANSCRIPT
                  </p>
                  <h2
                    id="conversation-history-heading"
                    className="mt-0.5 font-display text-base font-bold text-ink"
                  >
                    会話履歴
                  </h2>
                </div>
                <span className="rounded-full bg-paper px-2.5 py-1 text-xs font-bold text-ink-muted">
                  {finalTurnCount}件
                </span>
              </div>
              <p className="mt-1.5 text-xs leading-5 text-ink-muted">
                <Sparkles
                  aria-hidden="true"
                  size={12}
                  className="mr-1 inline text-primary"
                />
                印のある発言に触れると、その時の返答案を確認できます。
              </p>
            </div>

            <div
              ref={historyScrollRef}
              id="meeting-conversation-history"
              role="region"
              aria-label="会話履歴の内容"
              tabIndex={0}
              onScroll={updateHistoryFollowState}
              className="min-h-0 flex-1 space-y-2 overflow-y-auto overscroll-contain p-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
            >
              {finalTurnCount === 0 &&
              !state.interimOther &&
              !state.interimSelf ? (
                <div className="flex h-full min-h-40 flex-col items-center justify-center rounded-2xl border border-dashed border-line bg-paper px-5 text-center">
                  <Radio
                    aria-hidden="true"
                    size={20}
                    className="text-ink-faint"
                  />
                  <p className="mt-2 text-sm font-semibold text-ink-muted">
                    発言を待っています
                  </p>
                  <p className="mt-1 text-xs leading-5 text-ink-faint">
                    聞き取った内容がここに時系列で並びます。
                  </p>
                </div>
              ) : (
                <>
                  {turns.map((turn) => (
                    <ConversationTurn
                      key={turn.id}
                      turnId={turn.id}
                      speaker={turn.speaker}
                      text={turn.text}
                      suggestions={suggestionsForTurn(
                        state.suggestionCards,
                        turn.id,
                      )}
                      pinned={pinnedTurnId === turn.id}
                      onTogglePinned={() =>
                        setPinnedTurnId((current) =>
                          current === turn.id ? null : turn.id,
                        )
                      }
                    />
                  ))}
                  {state.interimOther && (
                    <ConversationTurn
                      speaker="other"
                      text={state.interimOther}
                      interim
                    />
                  )}
                  {state.interimSelf && (
                    <ConversationTurn
                      speaker="self"
                      text={state.interimSelf}
                      interim
                    />
                  )}
                </>
              )}
            </div>
          </section>

          <div className="grid min-h-0 grid-rows-[minmax(240px,1.12fr)_minmax(155px,0.88fr)] gap-3 max-[680px]:grid-rows-[minmax(340px,auto)_minmax(230px,auto)]">
            <section
              className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-cue/25 bg-surface p-3 shadow-raised"
              aria-label="現在の返答案"
            >
              <EmbeddedLiveReplyPanel
                state={state}
                send={send}
                onClose={onSettings}
                panelHeightClass="h-full"
                replyReadiness={replyReadiness}
              />
            </section>

            <section
              className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-sm"
              aria-labelledby="meeting-note-heading"
            >
              <div className="flex shrink-0 items-center gap-2.5 border-b border-line px-4 py-2.5">
                <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary-soft text-primary">
                  <BrainCircuit aria-hidden="true" size={16} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-[10px] font-bold tracking-[0.16em] text-primary">
                    MEETING MEMORY
                  </p>
                  <h2
                    id="meeting-note-heading"
                    className="font-display text-sm font-bold text-ink"
                  >
                    AIによる会話メモ
                  </h2>
                  <p className="mt-0.5 text-[11px] text-ink-muted">
                    確定した発言5件ごとに自動で整理します。
                  </p>
                </div>
                <Button
                  variant="quiet"
                  size="sm"
                  onClick={runInfo}
                  disabled={!canRunInfo}
                  className="min-h-8 px-2 text-xs text-primary"
                >
                  <RefreshCw
                    aria-hidden="true"
                    size={12}
                    className={
                      state.isResearchingInfo
                        ? "animate-spin motion-reduce:animate-none"
                        : ""
                    }
                  />
                  {state.isResearchingInfo ? "整理中" : "今すぐ整理"}
                </Button>
              </div>

              {infoUnavailableMessage && (
                <div className="flex shrink-0 items-center gap-2 border-b border-line bg-warning-soft/60 px-4 py-2 text-xs text-ink">
                  <p className="min-w-0 flex-1" role="status">
                    {infoUnavailableMessage}
                  </p>
                  {showInfoSettings && (
                    <Button
                      variant="quiet"
                      size="sm"
                      onClick={onSettings}
                      className="min-h-8"
                    >
                      設定を確認
                    </Button>
                  )}
                </div>
              )}

              <MeetingNoteSections note={state.session?.aiNote ?? ""} />
            </section>
          </div>
        </div>
      </div>
    </main>
  );
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

type MeetingNoteSectionId = "decisions" | "openItems" | "nextActions";

interface MeetingNoteSection {
  id: MeetingNoteSectionId;
  title: string;
  emptyText: string;
  content: string;
  icon: typeof CheckCircle2;
  toneClass: string;
}

const NOTE_SECTION_DEFINITIONS: Array<Omit<MeetingNoteSection, "content">> = [
  {
    id: "decisions",
    title: "決まったこと",
    emptyText: "まだありません",
    icon: CheckCircle2,
    toneClass: "bg-positive-soft text-positive",
  },
  {
    id: "openItems",
    title: "未確認・懸念",
    emptyText: "まだありません",
    icon: CircleAlert,
    toneClass: "bg-warning-soft text-warning",
  },
  {
    id: "nextActions",
    title: "次にすること",
    emptyText: "まだありません",
    icon: ListTodo,
    toneClass: "bg-primary-soft text-primary",
  },
];

const NOTE_HEADING_IDS: Record<string, MeetingNoteSectionId> = {
  決まったこと: "decisions",
  決定事項: "decisions",
  "未確認・懸念": "openItems",
  未確認事項: "openItems",
  懸念事項: "openItems",
  次にすること: "nextActions",
  次のアクション: "nextActions",
};

function parseMeetingNote(note: string): MeetingNoteSection[] {
  const contentById: Record<MeetingNoteSectionId, string[]> = {
    decisions: [],
    openItems: [],
    nextActions: [],
  };
  let activeId: MeetingNoteSectionId | null = null;
  let matchedHeading = false;

  for (const line of note.split(/\r?\n/)) {
    const heading = line.match(/^#{1,6}\s+(.+?)\s*$/)?.[1]?.trim();
    if (heading) {
      const nextId = NOTE_HEADING_IDS[heading];
      if (nextId) {
        activeId = nextId;
        matchedHeading = true;
      } else {
        activeId = null;
      }
      continue;
    }
    if (activeId) contentById[activeId].push(line);
  }

  if (note.trim() && !matchedHeading) {
    contentById.openItems.push(note.trim());
  }

  return NOTE_SECTION_DEFINITIONS.map((definition) => ({
    ...definition,
    content: contentById[definition.id].join("\n").trim(),
  }));
}

function MeetingNoteSections({ note }: { note: string }) {
  const sections = parseMeetingNote(note);
  return (
    <div
      className="min-h-0 flex-1 space-y-1.5 overflow-y-auto px-3 py-2.5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-primary"
      aria-label="会話メモ"
      aria-live="polite"
      tabIndex={0}
    >
      {sections.map((section) => {
        const Icon = section.icon;
        return (
          <section
            key={section.id}
            className="grid grid-cols-[28px_110px_minmax(0,1fr)] items-start gap-2 rounded-xl border border-line/80 bg-paper/55 px-2.5 py-2"
            aria-labelledby={`meeting-note-${section.id}`}
          >
            <span
              className={`flex h-7 w-7 items-center justify-center rounded-lg ${section.toneClass}`}
            >
              <Icon aria-hidden="true" size={14} />
            </span>
            <h3
              id={`meeting-note-${section.id}`}
              className="pt-1 text-xs font-bold text-ink"
            >
              {section.title}
            </h3>
            <p
              className={`whitespace-pre-wrap pt-0.5 text-xs leading-5 ${
                section.content ? "text-ink" : "text-ink-faint"
              }`}
            >
              {section.content || section.emptyText}
            </p>
          </section>
        );
      })}
    </div>
  );
}

function suggestionsForTurn(
  cards: SuggestionCard[],
  turnId: string,
): SuggestionCard[] {
  return cards
    .filter(
      (card) =>
        card.targetUtteranceId === turnId &&
        card.status === "ready" &&
        card.text.trim().length > 0,
    )
    .sort((left, right) => {
      if (left.agentPriority !== right.agentPriority)
        return left.agentPriority - right.agentPriority;
      return left.agentLabel.localeCompare(right.agentLabel);
    });
}

interface ConversationTurnProps {
  turnId?: string;
  speaker: Turn["speaker"];
  text: string;
  interim?: boolean;
  suggestions?: SuggestionCard[];
  pinned?: boolean;
  onTogglePinned?: () => void;
}

function ConversationTurn({
  turnId,
  speaker,
  text,
  interim = false,
  suggestions = [],
  pinned = false,
  onTogglePinned,
}: ConversationTurnProps) {
  const [copiedSuggestionId, setCopiedSuggestionId] = useState<string | null>(
    null,
  );
  const isOther = speaker === "other";
  const speakerLabel = isOther ? "相手" : "自分";
  const hasSuggestions = suggestions.length > 0;
  const panelId = turnId ? `turn-suggestions-${turnId}` : undefined;

  async function copySuggestion(suggestion: SuggestionCard) {
    try {
      await navigator.clipboard.writeText(suggestion.text);
      setCopiedSuggestionId(suggestion.suggestionId);
      window.setTimeout(() => setCopiedSuggestionId(null), 1600);
    } catch {
      setCopiedSuggestionId(null);
    }
  }

  const turnContent = (
    <>
      <div className="flex items-center gap-2">
        <p
          className={`text-[11px] font-bold ${isOther ? "text-cue" : "text-positive"}`}
        >
          {speakerLabel}
          {interim && "・聞き取り中"}
        </p>
        {hasSuggestions && (
          <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-primary-soft px-1.5 py-0.5 text-[10px] font-bold text-primary">
            <Sparkles aria-hidden="true" size={10} />
            {pinned ? "表示中" : "返答案"}
          </span>
        )}
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words text-left text-sm leading-6 text-ink">
        {text}
      </p>
    </>
  );

  return (
    <article
      className={`rounded-xl border transition-colors motion-reduce:transition-none ${
        isOther
          ? "border-cue/15 bg-cue-soft/65"
          : "border-positive/15 bg-positive-soft/65"
      } ${pinned ? "border-primary/35 ring-1 ring-primary/10" : ""}`}
      aria-live={interim ? "polite" : undefined}
      aria-atomic={interim || undefined}
    >
      {hasSuggestions ? (
        <Tooltip
          side="right"
          content={
            <div className="space-y-2 py-0.5">
              <p className="font-bold text-white">この発言への返答案</p>
              <p className="whitespace-pre-wrap text-xs leading-5 text-white">
                {suggestions[0].text}
              </p>
              <p className="text-[10px] text-white/60">
                クリックで履歴内に固定
              </p>
            </div>
          }
        >
          <button
            type="button"
            className="w-full cursor-help rounded-xl px-3 py-2.5 hover:bg-primary-soft/55 focus-visible:bg-primary-soft/55"
            aria-expanded={pinned}
            aria-controls={panelId}
            onClick={onTogglePinned}
          >
            {turnContent}
          </button>
        </Tooltip>
      ) : (
        <div className="px-3 py-2.5">{turnContent}</div>
      )}

      {pinned && hasSuggestions && (
        <div
          id={panelId}
          className="mx-2 mb-2 space-y-2 rounded-xl border border-primary/20 bg-surface p-2.5 shadow-sm"
        >
          <div className="flex items-center gap-1.5 text-[10px] font-bold tracking-[0.08em] text-primary">
            <Sparkles aria-hidden="true" size={11} />
            この時の返答案
          </div>
          {suggestions.map((suggestion) => (
            <div
              key={suggestion.suggestionId}
              className="rounded-lg bg-primary-soft/70 px-2.5 py-2"
            >
              {suggestions.length > 1 && (
                <p className="text-[10px] font-bold text-primary">
                  {suggestion.agentLabel}
                </p>
              )}
              <p className="whitespace-pre-wrap text-xs font-medium leading-5 text-ink">
                {suggestion.text}
              </p>
              <button
                type="button"
                onClick={() => void copySuggestion(suggestion)}
                className="mt-1.5 inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[10px] font-bold text-primary hover:bg-surface"
              >
                {copiedSuggestionId === suggestion.suggestionId ? (
                  <Check aria-hidden="true" size={11} />
                ) : (
                  <Clipboard aria-hidden="true" size={11} />
                )}
                {copiedSuggestionId === suggestion.suggestionId
                  ? "コピーしました"
                  : "コピー"}
              </button>
            </div>
          ))}
        </div>
      )}
    </article>
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
