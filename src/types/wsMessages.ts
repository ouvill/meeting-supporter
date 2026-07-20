import { z } from "zod";

const DeviceSchema = z.object({
  index: z.union([z.string(), z.number()]),
  name: z.string(),
  is_monitor: z.boolean(),
  is_default: z.boolean().optional(),
});

const TurnItemSchema = z.object({
  id: z.string(),
  speaker: z.string(),
  text: z.string(),
  speaker_id: z.string().nullable().optional(),
});

const SessionInfoSchema = z.object({
  type: z.literal("session_info"),
  id: z.string(),
  started_at: z.string(),
  title: z.string().nullable().optional(),
  ended_at: z.string().nullable().optional(),
  is_active: z.boolean(),
});

const ReplyAgentSettingsSchema = z.object({
  id: z.string(),
  label: z.string(),
  enabled: z.boolean(),
  priority: z.number(),
  model: z.string().nullable().optional(),
});

const SuggestionModeSchema = z.enum([
  "normal",
  "polite",
  "short",
  "clarify",
  "buy_time",
  "push_back",
  "summarize",
]);

export const InboundMessageSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("status"), text: z.string() }),
  z.object({ type: z.literal("meeting_state"), running: z.boolean() }),
  z.object({
    type: z.literal("stt_state"),
    backend: z.string(),
    initialized: z.boolean(),
    initializing: z.boolean(),
  }),
  z.object({
    type: z.literal("devices_list"),
    devices: z.array(DeviceSchema),
    current_other: z.union([z.string(), z.number()]).nullable(),
    current_self: z.union([z.string(), z.number()]).nullable(),
  }),
  z.object({
    type: z.literal("agent_settings"),
    reply_enabled: z.boolean(),
    reply_auto_generate: z.boolean().optional(),
    reply_agents: z.array(ReplyAgentSettingsSchema),
    info_enabled: z.boolean(),
  }),
  z.object({
    type: z.literal("history_reset"),
    items: z.array(TurnItemSchema),
  }),
  z.object({ type: z.literal("ai_note_updated"), text: z.string() }),
  z.object({ type: z.literal("error"), text: z.string() }),
  z.object({
    type: z.literal("audio_level"),
    role: z.string(),
    level: z.number(),
  }),
  z.object({
    type: z.literal("stt_interim"),
    role: z.string(),
    text: z.string(),
  }),
  z.object({
    type: z.literal("stream_info"),
    role: z.string(),
    device: z.string(),
    rate: z.number(),
  }),
  z.object({ type: z.literal("info_researching") }),
  z.object({ type: z.literal("info_researching_finished") }),
  z.object({
    type: z.literal("info_chunk"),
    text: z.string(),
    final: z.boolean(),
  }),
  z.object({
    type: z.literal("stt_final"),
    role: z.string(),
    text: z.string(),
    speaker_id: z.string().nullable(),
    utterance_id: z.string(),
  }),
  z.object({
    type: z.literal("suggestions_start"),
    agent_id: z.string(),
    agent_label: z.string(),
    agent_priority: z.number(),
    generation_id: z.string(),
    suggestion_id: z.string(),
    target_utterance_id: z.string(),
    target_role: z.string(),
    mode: SuggestionModeSchema.default("normal"),
  }),
  z.object({
    type: z.literal("reply_chunk"),
    text: z.string(),
    final: z.boolean(),
    agent_id: z.string(),
    agent_label: z.string(),
    agent_priority: z.number(),
    generation_id: z.string(),
    suggestion_id: z.string(),
    target_utterance_id: z.string(),
    target_role: z.string(),
    mode: SuggestionModeSchema.default("normal"),
  }),
  z.object({
    type: z.literal("suggestion_error"),
    text: z.string(),
    agent_id: z.string(),
    agent_label: z.string(),
    agent_priority: z.number(),
    generation_id: z.string(),
    suggestion_id: z.string(),
    target_utterance_id: z.string(),
    target_role: z.string(),
    mode: SuggestionModeSchema.default("normal"),
  }),
  z.object({
    type: z.literal("reply_cancel_result"),
    generation_id: z.string(),
    target_utterance_id: z.string(),
    status: z.enum(["applied", "not_applied"]),
    cancelled_suggestion_ids: z.array(z.string()),
  }),
  SessionInfoSchema,
]);

export type InboundMessage = z.infer<typeof InboundMessageSchema>;
