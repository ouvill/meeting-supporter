import { useState } from "react";
import { PanelRight } from "lucide-react";
import type { SendFn, SocketState, Turn } from "../types";
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
}

export function MainMeetingControlScreen({
  state,
  send,
  onSettings,
  replyReadiness,
}: Props) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const turns = state.session?.turns ?? EMPTY_TURNS;
  return (
    <main
      data-testid="meeting-control-screen"
      className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-surface px-4 pb-5 text-ink sm:px-7"
      aria-labelledby="meeting-control-heading"
    >
      <div className="mx-auto flex min-h-0 w-full max-w-[1240px] flex-1 flex-col gap-4">
        <MeetingControls state={state} send={send} />
        <div className="flex justify-end">
          <Button
            variant="quiet"
            size="sm"
            aria-expanded={historyOpen}
            aria-controls="meeting-history-pane"
            onClick={() => setHistoryOpen((value) => !value)}
          >
            <PanelRight className="size-4" aria-hidden="true" />
            会話履歴
          </Button>
        </div>
        <div
          className={`grid min-h-0 flex-1 gap-6 ${historyOpen ? "lg:grid-cols-[minmax(0,1.35fr)_minmax(280px,1fr)]" : "mx-auto w-full max-w-3xl"}`}
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
              className="flex min-h-[280px] min-w-0 flex-col border-t border-line pt-4 lg:min-h-0 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0"
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
