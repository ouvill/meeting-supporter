import { afterEach, describe, expect, it, vi } from "vitest";
import { ReplyLatency } from "./replyLatency";
import { InboundMessageSchema } from "../types/wsMessages";

afterEach(() => {
  vi.restoreAllMocks();
  performance.clearMeasures();
});

function latest(prefix: string) {
  const entries = performance
    .getEntriesByType("measure")
    .filter((entry) => entry.name.startsWith(prefix));
  return entries[entries.length - 1]?.toJSON();
}

describe("reply latency diagnostics", () => {
  it("separates the first rendered text, sentence and completion without retaining text", () => {
    let now = 100;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const timing = new ReplyLatency();
    timing.requested("synthetic-generation");
    now = 150;
    timing.rendered("synthetic-generation", " ", false);
    expect(latest("meeting.reply.manual.")).toBeUndefined();
    now = 250;
    timing.rendered("synthetic-generation", "合成の", false);
    now = 500;
    timing.rendered("synthetic-generation", "合成の返答です。", false);
    now = 900;
    timing.rendered("synthetic-generation", "合成の返答です。補足です。", true);
    expect(latest("meeting.reply.manual.")).toMatchObject({
      startTime: 100,
      duration: 800,
      detail: {
        first_text_render_ms: 150,
        first_sentence_render_ms: 400,
        outcome: "completed",
      },
    });
    const serialized = JSON.stringify(latest("meeting.reply.manual."));
    expect(serialized).not.toContain("合成");
    expect(serialized).not.toContain("synthetic-generation");
  });

  it("does not invent a button timestamp for an automatic or other-window generation", () => {
    new ReplyLatency().rendered("unrequested", "合成。", true);
    expect(latest("meeting.reply.manual.")).toBeUndefined();
  });

  it("uses completion for unpunctuated sentences and ignores late output after cancellation", () => {
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const timing = new ReplyLatency();
    timing.requested("one");
    now = 20;
    timing.rendered("one", "synthetic", false);
    now = 50;
    timing.rendered("one", "synthetic", true);
    expect(
      latest("meeting.reply.manual.").detail.first_sentence_render_ms,
    ).toBe(50);
    timing.requested("two");
    now = 70;
    timing.received({
      type: "reply_cancel_result",
      generation_id: "two",
      target_utterance_id: "target",
      status: "applied",
      cancelled_suggestion_ids: [],
    });
    now = 100;
    timing.rendered("two", "late synthetic。", true);
    expect(latest("meeting.reply.manual.")).toMatchObject({
      duration: 20,
      detail: { first_sentence_render_ms: null, outcome: "cancelled" },
    });
  });

  it("bounds diagnostics and rejects invalid backend durations", () => {
    const timing = new ReplyLatency();
    for (let index = 0; index < 80; index++) {
      timing.requested(`generation-${index}`);
      timing.rendered(`generation-${index}`, "合成。", true);
      timing.received(
        InboundMessageSchema.parse({
          type: "reply_timing",
          generation_id: `generation-${index}`,
          suggestion_id: "synthetic",
          preparation_ms: 1,
          first_text_ms: 2,
          first_sentence_ms: 3,
          total_ms: 4,
          outcome: "completed",
        }),
      );
    }
    expect(
      performance
        .getEntriesByType("measure")
        .filter((entry) => entry.name.startsWith("meeting.reply.")).length,
    ).toBe(100);
    expect(JSON.stringify(latest("meeting.reply.backend."))).not.toContain(
      "synthetic",
    );
    expect(
      InboundMessageSchema.safeParse({
        type: "reply_timing",
        generation_id: "one",
        suggestion_id: "two",
        preparation_ms: -1,
        first_text_ms: null,
        first_sentence_ms: null,
        total_ms: 4,
        outcome: "completed",
      }).success,
    ).toBe(false);
  });

  it("records disconnection without letting delayed UI updates resurrect timing", () => {
    const timing = new ReplyLatency();
    timing.requested("one");
    timing.disconnected();
    timing.rendered("one", "合成。", true);
    expect(latest("meeting.reply.manual.").detail.outcome).toBe("disconnected");
  });
});
