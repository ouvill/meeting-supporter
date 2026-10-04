import type { InboundMessage } from "../types/wsMessages";

const LIMIT = 50;
type Outcome =
  | "pending"
  | "completed"
  | "failed"
  | "cancelled"
  | "disconnected";
interface ManualTiming {
  name: string;
  started: number;
  firstText: number | null;
  firstSentence: number | null;
  outcome: Outcome;
}
type BackendTiming = Extract<InboundMessage, { type: "reply_timing" }>;

// Local duration diagnostics only. No transcript, reply text, model or identifiers
// are included in Performance entries, and each series/cache is bounded to 50 entries.
export class ReplyLatency {
  private manual = new Map<string, ManualTiming>();
  private manualSlot = 0;
  private backendSlot = 0;
  private backend = new Map<string, BackendTiming>();
  private listeners = new Set<() => void>();
  private revision = 0;

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  getRevision = () => this.revision;

  private changed() {
    this.revision += 1;
    for (const listener of this.listeners) listener();
  }

  constructor(
    private clock: Pick<
      Performance,
      "now" | "measure" | "clearMeasures"
    > = performance,
  ) {}

  requested(generationId: string) {
    if (this.manual.has(generationId)) return;
    const name = `meeting.reply.manual.${this.manualSlot++ % LIMIT}`;
    this.clock.clearMeasures(name);
    if (this.manual.size === LIMIT) {
      const oldest = this.manual.keys().next().value;
      if (oldest !== undefined) this.manual.delete(oldest);
    }
    this.manual.set(generationId, {
      name,
      started: this.clock.now(),
      firstText: null,
      firstSentence: null,
      outcome: "pending",
    });
    this.changed();
  }

  rendered(generationId: string | null, text: string, completed: boolean) {
    const timing = generationId ? this.manual.get(generationId) : undefined;
    if (!timing || timing.outcome !== "pending" || !text.trim()) return;
    const elapsed = this.clock.now() - timing.started;
    let changed = false;
    if (timing.firstText === null) {
      timing.firstText = elapsed;
      changed = true;
    }
    if (
      timing.firstSentence === null &&
      (/[。！？!?]/u.test(text) || completed)
    ) {
      timing.firstSentence = elapsed;
      changed = true;
    }
    if (completed) {
      timing.outcome = "completed";
      changed = true;
    }
    if (changed) this.write(timing);
  }

  details(generationId: string | null, suggestionId: string | null) {
    const manual = generationId ? this.manual.get(generationId) : undefined;
    const candidate = suggestionId ? this.backend.get(suggestionId) : undefined;
    const backend =
      candidate?.generation_id === generationId ? candidate : undefined;
    const difference = (
      end: number | null | undefined,
      start: number | null | undefined,
    ) => (end != null && start != null && end >= start ? end - start : null);
    return {
      firstTextMs: manual?.firstText ?? null,
      firstSentenceMs: manual?.firstSentence ?? null,
      preparationMs: backend?.preparation_ms ?? null,
      responseWaitMs: difference(
        backend?.first_text_ms,
        backend?.preparation_ms,
      ),
      sentenceStreamingMs: difference(
        backend?.first_sentence_ms,
        backend?.first_text_ms,
      ),
    };
  }

  received(message: InboundMessage) {
    if (message.type === "reply_timing") {
      this.backend.delete(message.suggestion_id);
      if (this.backend.size === LIMIT) {
        const oldest = this.backend.keys().next().value;
        if (oldest !== undefined) this.backend.delete(oldest);
      }
      this.backend.set(message.suggestion_id, message);
      const name = `meeting.reply.backend.${this.backendSlot++ % LIMIT}`;
      this.clock.clearMeasures(name);
      this.clock.measure(name, {
        start: Math.max(0, this.clock.now() - message.total_ms),
        duration: message.total_ms,
        detail: {
          preparation_ms: message.preparation_ms,
          first_text_ms: message.first_text_ms,
          first_sentence_ms: message.first_sentence_ms,
          total_ms: message.total_ms,
          outcome: message.outcome,
        },
      });
      this.changed();
    } else if (message.type === "suggestion_error") {
      this.finish(message.generation_id, "failed");
    } else if (
      message.type === "reply_cancel_result" &&
      message.status === "applied"
    ) {
      this.finish(message.generation_id, "cancelled");
    }
  }

  disconnected() {
    for (const id of this.manual.keys()) this.finish(id, "disconnected");
    this.manual.clear();
    this.backend.clear();
    this.changed();
  }

  private finish(id: string, outcome: Outcome) {
    const timing = this.manual.get(id);
    if (!timing || timing.outcome !== "pending") return;
    timing.outcome = outcome;
    this.write(timing);
  }

  private write(timing: ManualTiming) {
    this.clock.clearMeasures(timing.name);
    this.clock.measure(timing.name, {
      start: timing.started,
      end: this.clock.now(),
      detail: {
        first_text_render_ms: timing.firstText,
        first_sentence_render_ms: timing.firstSentence,
        outcome: timing.outcome,
      },
    });
    this.changed();
  }
}

export const replyLatency = new ReplyLatency();
