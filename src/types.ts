export type DeviceId = string | number | null;

export interface Device {
  index: string | number;
  name: string;
  is_monitor: boolean;
  is_default?: boolean;
}

export interface Turn {
  id: string;
  speaker: "other" | "self";
  text: string;
  speakerId?: string | null;
}

export type SuggestionMode =
  | "normal"
  | "polite"
  | "short"
  | "clarify"
  | "buy_time"
  | "push_back"
  | "summarize";
export type SuggestionCardStatus =
  | "generating"
  | "ready"
  | "cancelled"
  | "error";

export interface SuggestionCard {
  generationId: string;
  suggestionId: string;
  agentId: string;
  agentLabel: string;
  agentPriority: number;
  targetUtteranceId: string;
  targetRole: "other" | "self";
  mode: SuggestionMode;
  text: string;
  status: SuggestionCardStatus;
  errorText?: string | null;
}

export interface ReplyAgentSettings {
  id: string;
  label: string;
  enabled: boolean;
  priority: number;
  model?: string | null;
}

export interface AgentSettings {
  replyEnabled: boolean;
  replyAutoGenerate: boolean;
  replyAgents: ReplyAgentSettings[];
  infoEnabled: boolean;
}

export interface MeetingContextInput {
  scenario: string;
  userRole: string;
  counterpartRole?: string;
  objective: string;
  background?: string;
  tone?: string;
  constraints?: string;
  customInstructions?: string;
}

export type ReferenceDocumentStatus = "queued" | "parsed" | "failed";

export interface ReferenceDocumentInput {
  id: string;
  name: string;
  mimeType: string;
  sizeBytes: number;
  text?: string;
  contentBase64?: string;
  status: ReferenceDocumentStatus;
  error?: string | null;
}

export interface MeetingSession {
  id: string;
  startedAt: string;
  title?: string | null;
  endedAt?: string | null;
  isActive: boolean;
  turns: Turn[];
  aiNote: string;
  meetingContext?: MeetingContextInput | null;
  references?: ReferenceDocumentInput[];
}

export interface ReplyCancelResult {
  generationId: string;
  targetUtteranceId: string;
  status: "applied" | "not_applied";
  cancelledSuggestionIds: string[];
}

export interface SocketState {
  connected: boolean;
  statusText: string;
  isRunning: boolean;
  sttBackend: string;
  sttInitialized: boolean;
  sttInitializing: boolean;
  sttInitRequested: boolean;
  agentSettings: AgentSettings;
  devices: Device[];
  deviceOther: DeviceId;
  deviceSelf: DeviceId;
  session: MeetingSession | null;
  activeSuggestionTargetId: string | null;
  activeSuggestionGenerationId: string | null;
  suggestionCards: SuggestionCard[];
  replyText: string;
  isGeneratingReply: boolean;
  lastReplyCancelResult: ReplyCancelResult | null;
  cancelledSuggestionIds: string[];
  discardedGenerationIds: string[];
  isResearchingInfo: boolean;
  interimOther: string;
  interimSelf: string;
  levelOther: number;
  levelSelf: number;
}

export type SendFn = (msg: WsMessage) => void;

export type WsMessage =
  | { type: "set_device"; role: "other" | "self"; device: DeviceId }
  | { type: "init_stt" }
  | { type: "shutdown_stt" }
  | {
      type: "start_meeting";
      meeting_context?: MeetingContextInput;
      references?: ReferenceDocumentInput[];
    }
  | { type: "stop_meeting" }
  | { type: "manual_speech"; text: string }
  | { type: "user_reply"; text: string }
  | {
      type: "generate_reply";
      generation_id: string;
      target_utterance_id?: string | null;
      mode?: SuggestionMode;
    }
  | { type: "cancel_reply"; generation_id: string; target_utterance_id: string }
  | { type: "run_info" }
  | { type: "reload_context" };
