import { useState } from "react";
import type { SendFn, SocketState, Turn } from "../types";
import { EmbeddedLiveReplyPanel } from "./assistant/LiveReplySidePanel";
import { MeetingControls } from "./meeting/MeetingControls";
import { TranscriptPanel } from "./meeting/TranscriptPanel";
import type { ReplyReadiness } from "./meeting/types";

const EMPTY_TURNS: Turn[] = [];
const WIDE_WINDOW_QUERY = "(min-width: 1024px)";

/** The history starts open only where it fits beside the reply. */
function historyFitsBesideReply(): boolean {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia(WIDE_WINDOW_QUERY).matches
  );
}

interface Props {
  state: SocketState;
  send: SendFn;
  onSettings: () => void;
  replyReadiness?: ReplyReadiness;
}

export function MainMeetingControlScreen({
  state,
  send,
  onSettings,
  replyReadiness,
}: Props) {
  const [historyOpen, setHistoryOpen] = useState(historyFitsBesideReply);
  const turns = state.session?.turns ?? EMPTY_TURNS;
  return (
    <main
      data-testid="meeting-control-screen"
      className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-surface px-4 pb-5 text-ink sm:px-7"
      aria-label="会話ワークスペース"
    >
      <div className="mx-auto flex min-h-0 w-full max-w-[1240px] flex-1 flex-col gap-5">
        <MeetingControls
          state={state}
          send={send}
          historyOpen={historyOpen}
          onToggleHistory={() => setHistoryOpen((value) => !value)}
        />
        <div
          className={`grid min-h-0 flex-1 gap-6 ${historyOpen ? "lg:grid-cols-[minmax(0,1.2fr)_minmax(300px,1fr)]" : "mx-auto w-full max-w-3xl"}`}
        >
          <div className="flex min-h-[420px] min-w-0 flex-col overflow-hidden">
            <EmbeddedLiveReplyPanel
              state={state}
              send={send}
              onClose={onSettings}
              panelHeightClass="h-full"
              replyReadiness={replyReadiness}
            />
          </div>
          {historyOpen && (
            <div
              id="meeting-history-pane"
              className="flex min-h-[320px] min-w-0 flex-col border-t border-line pt-4 lg:min-h-0 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0"
            >
              <TranscriptPanel
                turns={turns}
                interimOther={state.interimOther}
                interimSelf={state.interimSelf}
                suggestionCards={state.suggestionCards}
              />
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
