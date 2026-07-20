import { create } from "zustand";
import type {
  AgentSettings,
  MeetingSession,
  SocketState,
  SuggestionCard,
  SuggestionMode,
} from "../types";
import type { InboundMessage } from "../types/wsMessages";

const MAX_SUGGESTION_TARGETS = 5;
const MAX_SUGGESTION_CARDS = 30;
const MAX_TERMINAL_IDS = 30;

const DEFAULT_AGENT_SETTINGS: AgentSettings = {
  replyEnabled: true,
  replyAutoGenerate: false,
  replyAgents: [
    { id: "standard", label: "標準", enabled: true, priority: 10, model: null },
  ],
  infoEnabled: true,
};

const INITIAL: SocketState = {
  connected: false,
  statusText: "接続中...",
  isRunning: false,
  sttBackend: "google",
  sttInitialized: false,
  sttInitializing: false,
  sttInitRequested: false,
  agentSettings: DEFAULT_AGENT_SETTINGS,
  devices: [],
  deviceOther: null,
  deviceSelf: null,
  session: null,
  activeSuggestionTargetId: null,
  activeSuggestionGenerationId: null,
  suggestionCards: [],
  replyText: "",
  isGeneratingReply: false,
  lastReplyCancelResult: null,
  cancelledSuggestionIds: [],
  discardedGenerationIds: [],
  isResearchingInfo: false,
  interimOther: "",
  interimSelf: "",
  levelOther: 0,
  levelSelf: 0,
};

function fallbackUtteranceId(role: string, text: string) {
  return `${role}:${text}:${Math.random().toString(36).slice(2, 10)}`;
}

function selectPrimaryReply(cards: SuggestionCard[]) {
  // Cards are already sorted by ascending agentPriority; lower numbers are higher priority.
  return (
    cards.find(
      (card) =>
        (card.status === "generating" || card.status === "ready") && card.text,
    )?.text ?? ""
  );
}

function sortSuggestionCards(cards: SuggestionCard[]) {
  return [...cards].sort((a, b) => {
    if (a.agentPriority !== b.agentPriority)
      return a.agentPriority - b.agentPriority;
    return a.agentLabel.localeCompare(b.agentLabel);
  });
}

function deriveSuggestionState(
  cards: SuggestionCard[],
  activeGenerationId: string | null,
) {
  const activeCards = activeGenerationId
    ? cards.filter((card) => card.generationId === activeGenerationId)
    : [];
  return {
    suggestionCards: cards,
    replyText: selectPrimaryReply(activeCards),
    isGeneratingReply: activeCards.some((card) => card.status === "generating"),
  };
}

function insertSortedCard(cards: SuggestionCard[], card: SuggestionCard) {
  return sortSuggestionCards([...cards, card]);
}

function pruneSuggestionCards(
  cards: SuggestionCard[],
  activeTargetId: string | null,
  turnIds: string[],
) {
  const recentTargetIds = new Set(turnIds.slice(-MAX_SUGGESTION_TARGETS));
  if (activeTargetId) recentTargetIds.add(activeTargetId);

  const recentCards = cards.filter((card) =>
    recentTargetIds.has(card.targetUtteranceId),
  );
  if (recentCards.length <= MAX_SUGGESTION_CARDS) return recentCards;

  const activeCards = activeTargetId
    ? recentCards.filter((card) => card.targetUtteranceId === activeTargetId)
    : [];
  const inactiveCards = activeTargetId
    ? recentCards.filter((card) => card.targetUtteranceId !== activeTargetId)
    : recentCards;
  const keptActiveCards = activeCards.slice(0, MAX_SUGGESTION_CARDS);
  const inactiveLimit = Math.max(
    0,
    MAX_SUGGESTION_CARDS - keptActiveCards.length,
  );
  const keptInactiveCards =
    inactiveLimit > 0 ? inactiveCards.slice(0, inactiveLimit) : [];
  return [...keptActiveCards, ...keptInactiveCards];
}

function appendBounded(values: string[], additions: string[]) {
  return [...new Set([...values, ...additions])].slice(-MAX_TERMINAL_IDS);
}

function readAgentSettings(msg: {
  reply_enabled: boolean;
  reply_auto_generate?: boolean;
  reply_agents: AgentSettings["replyAgents"];
  info_enabled: boolean;
}): AgentSettings {
  return {
    replyEnabled: msg.reply_enabled,
    replyAutoGenerate: msg.reply_auto_generate ?? false,
    replyAgents: [...msg.reply_agents].sort((a, b) => {
      if (a.priority !== b.priority) return a.priority - b.priority;
      return a.label.localeCompare(b.label);
    }),
    infoEnabled: msg.info_enabled,
  };
}

function reduce(s: SocketState, msg: InboundMessage): SocketState {
  switch (msg.type) {
    case "status":
      return { ...s, statusText: msg.text };

    case "meeting_state": {
      const running = msg.running;
      return {
        ...s,
        isRunning: running,
        ...(running
          ? {}
          : {
              activeSuggestionTargetId: null,
              activeSuggestionGenerationId: null,
              suggestionCards: [],
              replyText: "",
              isGeneratingReply: false,
              lastReplyCancelResult: null,
              cancelledSuggestionIds: [],
              discardedGenerationIds: [],
              interimOther: "",
              interimSelf: "",
              levelOther: 0,
              levelSelf: 0,
            }),
      };
    }

    case "agent_settings": {
      const agentSettings = readAgentSettings(msg);
      return {
        ...s,
        agentSettings,
        suggestionCards: agentSettings.replyEnabled
          ? s.suggestionCards
          : s.suggestionCards.filter((card) => card.status !== "generating"),
        activeSuggestionGenerationId: agentSettings.replyEnabled
          ? s.activeSuggestionGenerationId
          : null,
        replyText: agentSettings.replyEnabled ? s.replyText : "",
        isGeneratingReply: agentSettings.replyEnabled
          ? s.isGeneratingReply
          : false,
        isResearchingInfo: agentSettings.infoEnabled
          ? s.isResearchingInfo
          : false,
      };
    }

    case "devices_list":
      return {
        ...s,
        devices: msg.devices,
        deviceOther: msg.current_other,
        deviceSelf: msg.current_self,
      };

    case "audio_level":
      return msg.role === "other"
        ? { ...s, levelOther: msg.level }
        : { ...s, levelSelf: msg.level };

    case "stt_interim":
      return msg.role === "other"
        ? { ...s, interimOther: msg.text }
        : { ...s, interimSelf: msg.text };

    case "stt_final": {
      const speakerId = msg.speaker_id;
      const turnId =
        msg.utterance_id ?? fallbackUtteranceId(msg.role, msg.text);
      const turn = {
        id: turnId,
        speaker: msg.role as "other" | "self",
        text: msg.text,
        speakerId,
      };
      const session = s.session
        ? { ...s.session, turns: [...s.session.turns, turn] }
        : {
            id: "",
            startedAt: new Date().toISOString(),
            isActive: false,
            turns: [turn],
            aiNote: "",
          };
      return {
        ...s,
        session,
        ...(msg.role === "other" ? { interimOther: "" } : { interimSelf: "" }),
      };
    }

    case "suggestions_start": {
      if (
        s.discardedGenerationIds.includes(msg.generation_id) ||
        s.cancelledSuggestionIds.includes(msg.suggestion_id)
      )
        return s;

      const targetRole: SuggestionCard["targetRole"] =
        msg.target_role === "self" ? "self" : "other";
      const existing = s.suggestionCards.some(
        (card) => card.suggestionId === msg.suggestion_id,
      );
      const nextCards: SuggestionCard[] = existing
        ? s.suggestionCards
        : insertSortedCard(s.suggestionCards, {
            generationId: msg.generation_id,
            suggestionId: msg.suggestion_id,
            agentId: msg.agent_id,
            agentLabel: msg.agent_label,
            agentPriority: msg.agent_priority,
            targetUtteranceId: msg.target_utterance_id,
            targetRole,
            mode: msg.mode as SuggestionMode,
            text: "",
            status: "generating",
            errorText: null,
          });
      const prunedCards = pruneSuggestionCards(
        nextCards,
        msg.target_utterance_id,
        s.session?.turns.map((turn) => turn.id) ?? [],
      );

      return {
        ...s,
        activeSuggestionTargetId: msg.target_utterance_id,
        activeSuggestionGenerationId: msg.generation_id,
        ...deriveSuggestionState(prunedCards, msg.generation_id),
      };
    }

    case "reply_chunk": {
      if (
        s.discardedGenerationIds.includes(msg.generation_id) ||
        s.cancelledSuggestionIds.includes(msg.suggestion_id)
      )
        return s;

      const existingIdx = s.suggestionCards.findIndex(
        (card) => card.suggestionId === msg.suggestion_id,
      );
      if (
        existingIdx >= 0 &&
        s.suggestionCards[existingIdx].status !== "generating"
      )
        return s;

      const targetRole: SuggestionCard["targetRole"] =
        msg.target_role === "self" ? "self" : "other";
      const baseCard: SuggestionCard =
        existingIdx >= 0
          ? s.suggestionCards[existingIdx]
          : {
              generationId: msg.generation_id,
              suggestionId: msg.suggestion_id,
              agentId: msg.agent_id,
              agentLabel: msg.agent_label,
              agentPriority: msg.agent_priority,
              targetUtteranceId: msg.target_utterance_id,
              targetRole,
              mode: msg.mode as SuggestionMode,
              text: "",
              status: "generating",
              errorText: null,
            };
      const updatedCard: SuggestionCard = {
        ...baseCard,
        text: baseCard.text + msg.text,
        status: msg.final ? "ready" : "generating",
        mode: msg.mode as SuggestionMode,
        errorText: null,
      };
      const nextCards =
        existingIdx >= 0
          ? s.suggestionCards.map((card, idx) =>
              idx === existingIdx ? updatedCard : card,
            )
          : insertSortedCard(s.suggestionCards, updatedCard);
      const pruneTargetId =
        s.activeSuggestionTargetId ?? msg.target_utterance_id;
      const prunedCards = pruneSuggestionCards(
        nextCards,
        pruneTargetId,
        s.session?.turns.map((turn) => turn.id) ?? [msg.target_utterance_id],
      );

      return {
        ...s,
        ...deriveSuggestionState(prunedCards, s.activeSuggestionGenerationId),
      };
    }

    case "suggestion_error": {
      if (
        s.discardedGenerationIds.includes(msg.generation_id) ||
        s.cancelledSuggestionIds.includes(msg.suggestion_id)
      )
        return s;

      const targetRole: SuggestionCard["targetRole"] =
        msg.target_role === "self" ? "self" : "other";
      const existingIdx = s.suggestionCards.findIndex(
        (card) => card.suggestionId === msg.suggestion_id,
      );
      if (
        existingIdx >= 0 &&
        s.suggestionCards[existingIdx].status !== "generating"
      )
        return s;

      const baseCard: SuggestionCard =
        existingIdx >= 0
          ? s.suggestionCards[existingIdx]
          : {
              generationId: msg.generation_id,
              suggestionId: msg.suggestion_id,
              agentId: msg.agent_id,
              agentLabel: msg.agent_label,
              agentPriority: msg.agent_priority,
              targetUtteranceId: msg.target_utterance_id,
              targetRole,
              mode: msg.mode as SuggestionMode,
              text: "",
              status: "generating",
              errorText: null,
            };
      const updatedCard: SuggestionCard = {
        ...baseCard,
        status: "error",
        mode: msg.mode as SuggestionMode,
        errorText: msg.text,
      };
      const nextCards =
        existingIdx >= 0
          ? s.suggestionCards.map((card, idx) =>
              idx === existingIdx ? updatedCard : card,
            )
          : insertSortedCard(s.suggestionCards, updatedCard);
      const pruneTargetId =
        s.activeSuggestionTargetId ?? msg.target_utterance_id;
      const prunedCards = pruneSuggestionCards(
        nextCards,
        pruneTargetId,
        s.session?.turns.map((turn) => turn.id) ?? [msg.target_utterance_id],
      );

      return {
        ...s,
        ...deriveSuggestionState(prunedCards, s.activeSuggestionGenerationId),
      };
    }
    case "reply_cancel_result": {
      if (s.discardedGenerationIds.includes(msg.generation_id)) return s;
      const lastReplyCancelResult = {
        generationId: msg.generation_id,
        targetUtteranceId: msg.target_utterance_id,
        status: msg.status,
        cancelledSuggestionIds: [...msg.cancelled_suggestion_ids],
      };
      if (msg.status === "not_applied") {
        return { ...s, lastReplyCancelResult };
      }

      const cancelledSuggestionIds = appendBounded(
        s.cancelledSuggestionIds,
        msg.cancelled_suggestion_ids,
      );
      const cancelledIdSet = new Set(msg.cancelled_suggestion_ids);
      const nextCards = s.suggestionCards.map((card) =>
        card.generationId === msg.generation_id &&
        card.targetUtteranceId === msg.target_utterance_id &&
        cancelledIdSet.has(card.suggestionId)
          ? { ...card, status: "cancelled" as const }
          : card,
      );
      return {
        ...s,
        lastReplyCancelResult,
        cancelledSuggestionIds,
        ...deriveSuggestionState(nextCards, s.activeSuggestionGenerationId),
      };
    }

    case "info_researching":
      return { ...s, isResearchingInfo: true };

    case "info_researching_finished":
      return { ...s, isResearchingInfo: false };

    case "ai_note_updated": {
      const session = s.session ? { ...s.session, aiNote: msg.text } : null;
      return {
        ...s,
        session,
        isResearchingInfo: false,
      };
    }

    case "info_chunk": { // Legacy fallback — no longer emitted by the server but kept for safety.
      const session = s.session
        ? { ...s.session, aiNote: s.session.aiNote + msg.text }
        : null;
      return {
        ...s,
        session,
        isResearchingInfo: !msg.final,
      };
    }

    case "history_reset": {
      const turns = msg.items.map((i) => ({
        id: i.id ?? fallbackUtteranceId(i.speaker, i.text),
        speaker: i.speaker as "other" | "self",
        text: i.text,
        speakerId: i.speaker_id ?? null,
      }));
      const session: MeetingSession | null = s.session
        ? { ...s.session, turns }
        : {
            id: "",
            startedAt: new Date().toISOString(),
            isActive: false,
            turns,
            aiNote: "",
          };
      return {
        ...s,
        session,
        activeSuggestionTargetId: null,
        activeSuggestionGenerationId: null,
        suggestionCards: [],
        replyText: "",
        isGeneratingReply: false,
        lastReplyCancelResult: null,
        cancelledSuggestionIds: [],
        discardedGenerationIds: [],
      };
    }

    case "session_info": {
      const isNewSession = s.session === null || s.session.id !== msg.id;
      const session: MeetingSession = {
        id: msg.id,
        startedAt: msg.started_at,
        title: msg.title ?? null,
        endedAt: msg.ended_at ?? null,
        isActive: msg.is_active,
        turns: isNewSession ? [] : (s.session?.turns ?? []),
        aiNote: isNewSession ? "" : (s.session?.aiNote ?? ""),
      };
      return isNewSession
        ? {
            ...s,
            session,
            activeSuggestionTargetId: null,
            activeSuggestionGenerationId: null,
            suggestionCards: [],
            replyText: "",
            isGeneratingReply: false,
            lastReplyCancelResult: null,
            cancelledSuggestionIds: [],
            discardedGenerationIds: [],
          }
        : { ...s, session };
    }

    case "stt_state":
      return {
        ...s,
        sttBackend: msg.backend,
        sttInitialized: msg.initialized,
        sttInitializing: msg.initializing,
        sttInitRequested: false,
      };

    case "error":
      console.error("[WS]", msg.text);
      return {
        ...s,
        statusText: `エラー: ${msg.text}`,
        sttInitializing: false,
        sttInitRequested: false,
      };

    case "stream_info":
      // Not yet used in UI; acknowledged.
      return s;

    default:
      return s;
  }
}

interface MeetingStore extends SocketState {
  dispatch: (msg: InboundMessage) => void;
  setConnected: (v: boolean) => void;
  setSttInitRequested: (v: boolean) => void;
  discardActiveReply: () => void;
  reset: () => void;
}

export const useMeetingStore = create<MeetingStore>((set) => ({
  ...INITIAL,
  dispatch: (msg) => set((s) => reduce(s, msg)),
  setConnected: (v) => set((s) => ({ ...s, connected: v })),
  setSttInitRequested: (v) => set((s) => ({ ...s, sttInitRequested: v })),
  discardActiveReply: () =>
    set((s) => {
      const generationId = s.activeSuggestionGenerationId;
      if (!generationId) return s;
      const suggestionCards = s.suggestionCards.filter(
        (card) => card.generationId !== generationId,
      );
      return {
        ...s,
        activeSuggestionTargetId: null,
        activeSuggestionGenerationId: null,
        suggestionCards,
        replyText: "",
        isGeneratingReply: false,
        discardedGenerationIds: appendBounded(s.discardedGenerationIds, [
          generationId,
        ]),
      };
    }),
  reset: () => set(INITIAL),
}));
