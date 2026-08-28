import { useLayoutEffect, useRef, useState } from "react";
import { Check, Clipboard, Radio, Sparkles } from "lucide-react";
import type { SuggestionCard, Turn } from "../../types";
import { Tooltip } from "../ui";

interface Props {
  turns: Turn[];
  interimOther: string;
  interimSelf: string;
  suggestionCards: SuggestionCard[];
}

export function TranscriptPanel({
  turns,
  interimOther,
  interimSelf,
  suggestionCards,
}: Props) {
  const historyScrollRef = useRef<HTMLDivElement>(null);
  const shouldFollowHistoryRef = useRef(true);
  const [pinnedTurnId, setPinnedTurnId] = useState<string | null>(null);
  const finalTurnCount = turns.length;

  useLayoutEffect(() => {
    if (!shouldFollowHistoryRef.current) return;
    const panel = historyScrollRef.current;
    if (panel) panel.scrollTop = panel.scrollHeight;
  }, [turns, interimOther, interimSelf]);

  function updateHistoryFollowState() {
    const panel = historyScrollRef.current;
    if (!panel) return;
    shouldFollowHistoryRef.current =
      panel.scrollHeight - panel.scrollTop - panel.clientHeight <= 24;
  }

  return (
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
        {finalTurnCount === 0 && !interimOther && !interimSelf ? (
          <div className="flex h-full min-h-40 flex-col items-center justify-center rounded-2xl border border-dashed border-line bg-paper px-5 text-center">
            <Radio aria-hidden="true" size={20} className="text-ink-faint" />
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
                suggestions={suggestionsForTurn(suggestionCards, turn.id)}
                pinned={pinnedTurnId === turn.id}
                onTogglePinned={() =>
                  setPinnedTurnId((current) =>
                    current === turn.id ? null : turn.id,
                  )
                }
              />
            ))}
            {interimOther && (
              <ConversationTurn
                speaker="other"
                text={interimOther}
                interim
              />
            )}
            {interimSelf && (
              <ConversationTurn speaker="self" text={interimSelf} interim />
            )}
          </>
        )}
      </div>
    </section>
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
