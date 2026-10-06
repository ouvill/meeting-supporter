import { useLayoutEffect, useRef, useState } from "react";
import { ArrowDown, Check, Clipboard, Sparkles } from "lucide-react";
import type { SuggestionCard, Turn } from "../../types";
import { cn } from "../ui";

interface Props {
  turns: Turn[];
  interimOther: string;
  interimSelf: string;
  suggestionCards: SuggestionCard[];
}

const FOLLOW_THRESHOLD_PX = 24;

export function TranscriptPanel({
  turns,
  interimOther,
  interimSelf,
  suggestionCards,
}: Props) {
  const historyScrollRef = useRef<HTMLDivElement>(null);
  const shouldFollowHistoryRef = useRef(true);
  const [following, setFollowing] = useState(true);
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
    const atLatest =
      panel.scrollHeight - panel.scrollTop - panel.clientHeight <=
      FOLLOW_THRESHOLD_PX;
    shouldFollowHistoryRef.current = atLatest;
    setFollowing(atLatest);
  }

  function scrollToLatest() {
    const panel = historyScrollRef.current;
    if (!panel) return;
    panel.scrollTop = panel.scrollHeight;
    shouldFollowHistoryRef.current = true;
    setFollowing(true);
  }

  const lastFinalSpeaker = turns[turns.length - 1]?.speaker;

  return (
    <section
      className="relative flex min-h-0 flex-1 flex-col overflow-hidden bg-surface"
      aria-labelledby="conversation-history-heading"
    >
      <div className="flex shrink-0 items-baseline gap-2 pb-2">
        <h2
          id="conversation-history-heading"
          className="text-sm font-bold text-ink"
        >
          会話履歴
        </h2>
        <span className="text-xs text-ink-muted">{finalTurnCount}件</span>
      </div>

      <div
        ref={historyScrollRef}
        id="meeting-conversation-history"
        role="region"
        aria-label="会話履歴の内容"
        tabIndex={0}
        onScroll={updateHistoryFollowState}
        className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain rounded-lg pb-2 pr-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
      >
        {finalTurnCount === 0 && !interimOther && !interimSelf ? (
          <div className="flex min-h-40 flex-1 flex-col items-center justify-center px-5 text-center">
            <p className="text-sm font-semibold text-ink-muted">
              発言を待っています
            </p>
            <p className="mt-1 text-xs leading-5 text-ink-faint">
              聞き取った内容がここに時系列で並びます。
            </p>
          </div>
        ) : (
          <>
            {turns.map((turn, index) => (
              <ConversationTurn
                key={turn.id}
                turnId={turn.id}
                speaker={turn.speaker}
                text={turn.text}
                continued={turns[index - 1]?.speaker === turn.speaker}
                first={index === 0}
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
                first={finalTurnCount === 0}
                interim
              />
            )}
            {interimSelf && (
              <ConversationTurn
                speaker="self"
                text={interimSelf}
                first={finalTurnCount === 0 && !interimOther}
                continued={!interimOther && lastFinalSpeaker === "self"}
                interim
              />
            )}
          </>
        )}
      </div>

      {!following && (
        <button
          type="button"
          onClick={scrollToLatest}
          className="absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1.5 text-xs font-semibold text-ink shadow-raised hover:border-primary/45 hover:text-primary"
        >
          <ArrowDown aria-hidden="true" className="size-3.5" />
          最新の発言へ
        </button>
      )}
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
  /** Same speaker as the entry above: the label stays for screen readers only. */
  continued?: boolean;
  first?: boolean;
  interim?: boolean;
  suggestions?: SuggestionCard[];
  pinned?: boolean;
  onTogglePinned?: () => void;
}

function ConversationTurn({
  turnId,
  speaker,
  text,
  continued = false,
  first = false,
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
  const groupWithPrevious = continued && !interim;

  async function copySuggestion(suggestion: SuggestionCard) {
    try {
      await navigator.clipboard.writeText(suggestion.text);
      setCopiedSuggestionId(suggestion.suggestionId);
      window.setTimeout(() => setCopiedSuggestionId(null), 1600);
    } catch {
      setCopiedSuggestionId(null);
    }
  }

  const bubbleClass = cn(
    "max-w-[88%] rounded-2xl px-3.5 py-2 text-left",
    isOther ? "rounded-tl-md" : "rounded-tr-md",
    interim
      ? "border border-dashed border-line-strong bg-surface"
      : isOther
        ? "bg-surface-muted"
        : "bg-primary-soft",
  );
  const bubbleText = (
    <p
      className={cn(
        "whitespace-pre-wrap break-words text-sm leading-6",
        interim ? "text-ink-muted" : "text-ink",
      )}
    >
      {text}
    </p>
  );

  return (
    <article
      className={cn(
        "flex min-w-0 flex-col",
        isOther ? "items-start" : "items-end",
        first ? "mt-0" : groupWithPrevious ? "mt-1" : "mt-3.5",
      )}
      aria-live={interim ? "polite" : undefined}
      aria-atomic={interim || undefined}
    >
      <p
        className={cn(
          "mb-1 px-1 text-xs font-bold",
          isOther ? "text-ink-muted" : "text-primary",
          groupWithPrevious && "sr-only",
        )}
      >
        {speakerLabel}
        {interim && "・聞き取り中"}
      </p>
      {hasSuggestions ? (
        <button
          type="button"
          className={cn(
            bubbleClass,
            "cursor-pointer transition-shadow hover:shadow-card motion-reduce:transition-none",
            pinned && "ring-2 ring-primary/35",
          )}
          aria-expanded={pinned}
          aria-controls={panelId}
          onClick={onTogglePinned}
        >
          {bubbleText}
          <span className="mt-1 flex items-center gap-1 text-xs font-semibold text-primary">
            <Sparkles aria-hidden="true" className="size-3" />
            {pinned ? "返答案を閉じる" : "返答案を見る"}
          </span>
        </button>
      ) : (
        <div className={bubbleClass}>{bubbleText}</div>
      )}

      {pinned && hasSuggestions && (
        <div
          id={panelId}
          className="mt-1.5 w-full max-w-[88%] space-y-2 self-start rounded-xl border border-primary/25 bg-surface px-3.5 py-2.5"
        >
          <p className="flex items-center gap-1.5 text-xs font-bold text-primary">
            <Sparkles aria-hidden="true" className="size-3" />
            この時の返答案
          </p>
          {suggestions.map((suggestion) => (
            <div key={suggestion.suggestionId}>
              {suggestions.length > 1 && (
                <p className="text-xs font-bold text-ink-muted">
                  {suggestion.agentLabel}
                </p>
              )}
              <p className="whitespace-pre-wrap text-sm leading-6 text-ink">
                {suggestion.text}
              </p>
              <button
                type="button"
                onClick={() => void copySuggestion(suggestion)}
                className="-ml-1.5 mt-1 inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs font-semibold text-primary hover:bg-primary-soft"
              >
                {copiedSuggestionId === suggestion.suggestionId ? (
                  <Check aria-hidden="true" className="size-3.5" />
                ) : (
                  <Clipboard aria-hidden="true" className="size-3.5" />
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
