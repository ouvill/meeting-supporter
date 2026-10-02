import type { SendFn, SocketState, Turn } from "../types";
import { EmbeddedLiveReplyPanel } from "./assistant/LiveReplySidePanel";
import { MeetingControls } from "./meeting/MeetingControls";
import { TranscriptPanel } from "./meeting/TranscriptPanel";
import type { ReplyReadiness } from "./meeting/types";

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
  const turns = state.session?.turns ?? EMPTY_TURNS;
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
          <div className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-cue/25 bg-surface p-3 shadow-raised max-[680px]:min-h-[340px]">
            <EmbeddedLiveReplyPanel
              state={state}
              send={send}
              onClose={onSettings}
              panelHeightClass="h-full"
              replyReadiness={replyReadiness}
            />
          </div>
        </div>
      </div>
    </main>
  );
}
