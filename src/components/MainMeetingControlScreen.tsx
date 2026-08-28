import {
  BrainCircuit,
  CheckCircle2,
  CircleAlert,
  ListTodo,
  RefreshCw,
} from "lucide-react";
import type { SendFn, SocketState, Turn } from "../types";
import type { AiUseCaseRouteStatus } from "../hooks/useAiRoutes";
import { EmbeddedLiveReplyPanel } from "./assistant/LiveReplySidePanel";
import { MeetingControls } from "./meeting/MeetingControls";
import { TranscriptPanel } from "./meeting/TranscriptPanel";
import type { ReplyReadiness } from "./meeting/types";
import { Button } from "./ui";

const EMPTY_TURNS: Turn[] = [];

interface Props {
  state: SocketState;
  send: SendFn;
  onSettings: () => void;
  replyReadiness?: ReplyReadiness;
  infoRouteStatus?: AiUseCaseRouteStatus;
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
  const turns = state.session?.turns ?? EMPTY_TURNS;
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

  return (
    <main
      data-testid="meeting-control-screen"
      className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-paper px-4 pb-4 pt-3 text-ink sm:px-5"
      aria-labelledby="meeting-control-heading"
    >
      <div className="mx-auto flex min-h-0 w-full max-w-[1240px] flex-1 flex-col gap-3">
        <MeetingControls state={state} send={send} />

        <div className="grid min-h-0 flex-1 grid-cols-[minmax(250px,0.85fr)_minmax(360px,1.15fr)] gap-3 max-[680px]:grid-cols-1 max-[680px]:overflow-y-auto">
          <TranscriptPanel
            turns={turns}
            interimOther={state.interimOther}
            interimSelf={state.interimSelf}
            suggestionCards={state.suggestionCards}
          />

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

