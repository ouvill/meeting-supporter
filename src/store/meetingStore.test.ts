import { beforeEach, describe, expect, it, vi } from "vitest";
import { useMeetingStore } from "./meetingStore";

describe("meetingStore", () => {
  beforeEach(() => {
    useMeetingStore.getState().reset();
    useMeetingStore.setState({
      ...useMeetingStore.getInitialState(),
      session: null,
      suggestionCards: [],
      interimOther: "",
      interimSelf: "",
      replyText: "",
    });
  });

  it("dispatches status message", () => {
    useMeetingStore.getState().dispatch({ type: "status", text: "Ready" });
    expect(useMeetingStore.getState().statusText).toBe("Ready");
  });

  it("dispatches meeting_state running", () => {
    useMeetingStore
      .getState()
      .dispatch({ type: "meeting_state", running: true });
    expect(useMeetingStore.getState().isRunning).toBe(true);
  });

  it("dispatches meeting_state stopped resets interim and levels", () => {
    const store = useMeetingStore.getState();
    store.dispatch({ type: "stt_interim", role: "other", text: "hello" });
    store.dispatch({ type: "audio_level", role: "other", level: 0.5 });
    store.dispatch({ type: "meeting_state", running: false });

    const s = useMeetingStore.getState();
    expect(s.isRunning).toBe(false);
    expect(s.interimOther).toBe("");
    expect(s.levelOther).toBe(0);
  });

  it("clears the info research state without replacing the note", () => {
    const store = useMeetingStore.getState();
    store.dispatch({
      type: "session_info",
      id: "session-info",
      started_at: "2026-07-19T09:00:00.000Z",
      is_active: true,
    });
    store.dispatch({ type: "ai_note_updated", text: "保持するメモ" });
    store.dispatch({ type: "info_researching" });

    store.dispatch({ type: "info_researching_finished" });

    const state = useMeetingStore.getState();
    expect(state.isResearchingInfo).toBe(false);
    expect(state.session?.aiNote).toBe("保持するメモ");
  });

  it("clears completed proposals at meeting end and when switching sessions", () => {
    const store = useMeetingStore.getState();
    store.dispatch({
      type: "session_info",
      id: "session-before-end",
      started_at: "2026-07-10T09:00:00.000Z",
      is_active: true,
    });
    store.dispatch({
      type: "suggestions_start",
      generation_id: "generation-1",
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-before-end",
      target_utterance_id: "target-before-end",
      target_role: "other",
      mode: "normal",
    });
    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-1",
      text: "会議終了前の提案です。",
      final: true,
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-before-end",
      target_utterance_id: "target-before-end",
      target_role: "other",
      mode: "normal",
    });

    let s = useMeetingStore.getState();
    expect(s.activeSuggestionTargetId).toBe("target-before-end");
    expect(s.suggestionCards).toHaveLength(1);
    expect(s.replyText).toBe("会議終了前の提案です。");
    expect(s.isGeneratingReply).toBe(false);

    store.dispatch({ type: "meeting_state", running: false });

    s = useMeetingStore.getState();
    expect(s.activeSuggestionTargetId).toBeNull();
    expect(s.suggestionCards).toEqual([]);
    expect(s.replyText).toBe("");
    expect(s.isGeneratingReply).toBe(false);

    store.dispatch({
      type: "suggestions_start",
      generation_id: "generation-1",
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-before-switch",
      target_utterance_id: "target-before-switch",
      target_role: "other",
      mode: "normal",
    });
    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-1",
      text: "セッション切替前の提案です。",
      final: true,
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-before-switch",
      target_utterance_id: "target-before-switch",
      target_role: "other",
      mode: "normal",
    });

    s = useMeetingStore.getState();
    expect(s.activeSuggestionTargetId).toBe("target-before-switch");
    expect(s.suggestionCards).toHaveLength(1);
    expect(s.replyText).toBe("セッション切替前の提案です。");
    expect(s.isGeneratingReply).toBe(false);

    store.dispatch({
      type: "session_info",
      id: "session-after-switch",
      started_at: "2026-07-10T10:00:00.000Z",
      is_active: true,
    });

    s = useMeetingStore.getState();
    expect(s.session?.id).toBe("session-after-switch");
    expect(s.activeSuggestionTargetId).toBeNull();
    expect(s.suggestionCards).toEqual([]);
    expect(s.replyText).toBe("");
    expect(s.isGeneratingReply).toBe(false);
  });

  it("dispatches devices_list", () => {
    const devices = [{ index: 0, name: "Mic", is_monitor: false }];
    useMeetingStore.getState().dispatch({
      type: "devices_list",
      devices,
      current_other: 0,
      current_self: null,
    });
    const s = useMeetingStore.getState();
    expect(s.devices).toEqual(devices);
    expect(s.deviceOther).toBe(0);
    expect(s.deviceSelf).toBeNull();
  });

  it("dispatches stt_final for other", () => {
    useMeetingStore.getState().dispatch({
      type: "stt_final",
      role: "other",
      text: "Hello",
      speaker_id: null,
      utterance_id: "u1",
    });
    const s = useMeetingStore.getState();
    expect(s.session?.turns).toHaveLength(1);
    expect(s.session?.turns[0].text).toBe("Hello");
    expect(s.session?.turns[0].speaker).toBe("other");
    expect(s.interimOther).toBe("");
  });

  it("dispatches stt_final for self", () => {
    useMeetingStore.getState().dispatch({
      type: "stt_final",
      role: "self",
      text: "Hi",
      speaker_id: null,
      utterance_id: "u2",
    });
    const s = useMeetingStore.getState();
    expect(s.session?.turns[0].speaker).toBe("self");
    expect(s.interimSelf).toBe("");
  });

  it("keeps a completed live reply candidate active for an other-side final turn", () => {
    const store = useMeetingStore.getState();

    store.dispatch({
      type: "stt_final",
      role: "other",
      text: "導入日はいつにできますか？",
      speaker_id: null,
      utterance_id: "turn-live",
    });
    store.dispatch({
      type: "suggestions_start",
      generation_id: "generation-1",
      agent_id: "reply_main",
      agent_label: "標準",
      agent_priority: 10,
      suggestion_id: "suggestion-live",
      target_utterance_id: "turn-live",
      target_role: "other",
      mode: "normal",
    });
    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-1",
      text: "来週水曜に",
      final: false,
      agent_id: "reply_main",
      agent_label: "標準",
      agent_priority: 10,
      suggestion_id: "suggestion-live",
      target_utterance_id: "turn-live",
      target_role: "other",
      mode: "normal",
    });
    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-1",
      text: "導入できます。",
      final: true,
      agent_id: "reply_main",
      agent_label: "標準",
      agent_priority: 10,
      suggestion_id: "suggestion-live",
      target_utterance_id: "turn-live",
      target_role: "other",
      mode: "normal",
    });

    const s = useMeetingStore.getState();
    const turns = s.session?.turns ?? [];
    expect(turns[turns.length - 1]).toEqual({
      id: "turn-live",
      speaker: "other",
      text: "導入日はいつにできますか？",
      speakerId: null,
    });
    expect(s.activeSuggestionTargetId).toBe("turn-live");
    expect(s.replyText).toBe("来週水曜に導入できます。");
    expect(s.isGeneratingReply).toBe(false);
    expect(s.suggestionCards).toEqual([
      {
        generationId: "generation-1",
        suggestionId: "suggestion-live",
        agentId: "reply_main",
        agentLabel: "標準",
        agentPriority: 10,
        targetUtteranceId: "turn-live",
        targetRole: "other",
        mode: "normal",
        text: "来週水曜に導入できます。",
        status: "ready",
        errorText: null,
      },
    ]);
  });

  it("keeps a completed proposal selected across later other, self, and interim transcription", () => {
    const store = useMeetingStore.getState();

    store.dispatch({
      type: "suggestions_start",
      generation_id: "generation-1",
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-completed",
      target_utterance_id: "target-completed",
      target_role: "other",
      mode: "normal",
    });
    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-1",
      text: "完了した提案です。",
      final: true,
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-completed",
      target_utterance_id: "target-completed",
      target_role: "other",
      mode: "normal",
    });
    store.dispatch({
      type: "stt_final",
      role: "other",
      text: "次の相手の発言です。",
      speaker_id: null,
      utterance_id: "turn-other-later",
    });
    store.dispatch({
      type: "stt_final",
      role: "self",
      text: "次の自分の発言です。",
      speaker_id: null,
      utterance_id: "turn-self-later",
    });
    store.dispatch({
      type: "stt_interim",
      role: "other",
      text: "さらに聞き取り中です。",
    });
    store.dispatch({
      type: "stt_interim",
      role: "self",
      text: "自分も聞き取り中です。",
    });

    const s = useMeetingStore.getState();
    expect(s.activeSuggestionTargetId).toBe("target-completed");
    expect(s.replyText).toBe("完了した提案です。");
    expect(s.isGeneratingReply).toBe(false);
    expect(s.interimOther).toBe("さらに聞き取り中です。");
    expect(s.interimSelf).toBe("自分も聞き取り中です。");
  });

  it("switches displayed generation to a new target without mixing completed target chunks", () => {
    const store = useMeetingStore.getState();

    store.dispatch({
      type: "stt_final",
      role: "other",
      text: "完了した対象の発言です。",
      speaker_id: null,
      utterance_id: "target-completed",
    });
    store.dispatch({
      type: "suggestions_start",
      generation_id: "generation-completed",
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-completed",
      target_utterance_id: "target-completed",
      target_role: "other",
      mode: "normal",
    });
    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-completed",
      text: "完了した提案です。",
      final: true,
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-completed",
      target_utterance_id: "target-completed",
      target_role: "other",
      mode: "normal",
    });
    store.dispatch({
      type: "stt_final",
      role: "self",
      text: "新しい対象の発言です。",
      speaker_id: null,
      utterance_id: "target-new",
    });
    store.dispatch({
      type: "suggestions_start",
      generation_id: "generation-new",
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-new",
      target_utterance_id: "target-new",
      target_role: "self",
      mode: "normal",
    });

    let s = useMeetingStore.getState();
    expect(s.activeSuggestionTargetId).toBe("target-new");
    expect(s.replyText).toBe("");
    expect(s.isGeneratingReply).toBe(true);

    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-new",
      text: "新しい提案の前半",
      final: false,
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-new",
      target_utterance_id: "target-new",
      target_role: "self",
      mode: "normal",
    });
    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-new",
      text: "です。",
      final: true,
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-new",
      target_utterance_id: "target-new",
      target_role: "self",
      mode: "normal",
    });

    s = useMeetingStore.getState();
    expect(s.replyText).toBe("新しい提案の前半です。");
    expect(s.isGeneratingReply).toBe(false);
    expect(
      s.suggestionCards.find(
        (card) => card.suggestionId === "suggestion-completed",
      ),
    ).toMatchObject({
      targetUtteranceId: "target-completed",
      text: "完了した提案です。",
      status: "ready",
    });
    expect(
      s.suggestionCards.find((card) => card.suggestionId === "suggestion-new"),
    ).toMatchObject({
      targetUtteranceId: "target-new",
      text: "新しい提案の前半です。",
      status: "ready",
    });
  });

  it("dispatches suggestions_start and reply_chunk", () => {
    const store = useMeetingStore.getState();
    store.dispatch({
      type: "suggestions_start",
      generation_id: "generation-1",
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "s1",
      target_utterance_id: "u1",
      target_role: "other",
      mode: "normal",
    });

    let s = useMeetingStore.getState();
    expect(s.suggestionCards).toHaveLength(1);
    expect(s.isGeneratingReply).toBe(true);

    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-1",
      text: "How about",
      final: false,
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "s1",
      target_utterance_id: "u1",
      target_role: "other",
      mode: "normal",
    });

    s = useMeetingStore.getState();
    expect(s.suggestionCards[0].text).toBe("How about");
    expect(s.replyText).toBe("How about");
  });

  it("dispatches suggestion_error", () => {
    const store = useMeetingStore.getState();
    store.dispatch({
      type: "suggestions_start",
      generation_id: "generation-1",
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "s1",
      target_utterance_id: "u1",
      target_role: "other",
      mode: "normal",
    });

    store.dispatch({
      type: "suggestion_error",
      generation_id: "generation-1",
      text: "Timeout",
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "s1",
      target_utterance_id: "u1",
      target_role: "other",
      mode: "normal",
    });

    const s = useMeetingStore.getState();
    expect(s.suggestionCards[0].status).toBe("error");
    expect(s.suggestionCards[0].errorText).toBe("Timeout");
    expect(s.activeSuggestionTargetId).toBe("u1");
    expect(s.replyText).toBe("");
    expect(s.isGeneratingReply).toBe(false);
  });

  it("dispatches agent_settings disable reply", () => {
    const store = useMeetingStore.getState();
    store.dispatch({
      type: "suggestions_start",
      generation_id: "generation-1",
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "s1",
      target_utterance_id: "u1",
      target_role: "other",
      mode: "normal",
    });

    store.dispatch({
      type: "agent_settings",
      reply_enabled: false,
      reply_auto_generate: true,
      reply_agents: [
        {
          id: "reply_custom",
          label: "Custom",
          enabled: true,
          priority: 5,
          model: null,
        },
      ],
      info_enabled: true,
    });

    const s = useMeetingStore.getState();
    expect(s.agentSettings.replyEnabled).toBe(false);
    expect(s.agentSettings.replyAutoGenerate).toBe(true);
    expect(s.agentSettings.replyAgents[0].id).toBe("reply_custom");
    expect(s.suggestionCards).toHaveLength(0);
    expect(s.replyText).toBe("");
  });

  it("uses first prioritized suggestion as primary reply", () => {
    const store = useMeetingStore.getState();
    store.dispatch({
      type: "suggestions_start",
      generation_id: "generation-1",
      agent_id: "reply_custom",
      agent_label: "Custom",
      agent_priority: 5,
      suggestion_id: "s-custom",
      target_utterance_id: "u1",
      target_role: "other",
      mode: "normal",
    });
    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-1",
      text: "Custom reply",
      final: true,
      agent_id: "reply_custom",
      agent_label: "Custom",
      agent_priority: 5,
      suggestion_id: "s-custom",
      target_utterance_id: "u1",
      target_role: "other",
      mode: "normal",
    });
    store.dispatch({
      type: "suggestions_start",
      generation_id: "generation-1",
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 10,
      suggestion_id: "s-main",
      target_utterance_id: "u1",
      target_role: "other",
      mode: "normal",
    });
    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-1",
      text: "Main reply",
      final: true,
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 10,
      suggestion_id: "s-main",
      target_utterance_id: "u1",
      target_role: "other",
      mode: "normal",
    });

    expect(useMeetingStore.getState().replyText).toBe("Custom reply");
  });

  it("marks applied cancellations terminal and ignores their late final chunks", () => {
    const store = useMeetingStore.getState();
    store.dispatch({
      type: "suggestions_start",
      generation_id: "generation-cancel",
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-cancel",
      target_utterance_id: "turn-cancel",
      target_role: "other",
      mode: "normal",
    });
    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-cancel",
      text: "保存しない途中結果",
      final: false,
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-cancel",
      target_utterance_id: "turn-cancel",
      target_role: "other",
      mode: "normal",
    });
    store.dispatch({
      type: "reply_cancel_result",
      generation_id: "generation-cancel",
      target_utterance_id: "turn-cancel",
      status: "applied",
      cancelled_suggestion_ids: ["suggestion-cancel"],
    });

    let state = useMeetingStore.getState();
    expect(state.suggestionCards[0].status).toBe("cancelled");
    expect(state.isGeneratingReply).toBe(false);
    expect(state.replyText).toBe("");
    expect(state.lastReplyCancelResult).toEqual({
      generationId: "generation-cancel",
      targetUtteranceId: "turn-cancel",
      status: "applied",
      cancelledSuggestionIds: ["suggestion-cancel"],
    });

    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-cancel",
      text: "遅延した完成結果",
      final: true,
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-cancel",
      target_utterance_id: "turn-cancel",
      target_role: "other",
      mode: "normal",
    });

    state = useMeetingStore.getState();
    expect(state.suggestionCards[0].text).toBe("保存しない途中結果");
    expect(state.suggestionCards[0].status).toBe("cancelled");
    expect(state.replyText).toBe("");
  });

  it("keeps late messages from an older generation out of the active reply", () => {
    const store = useMeetingStore.getState();
    for (const generationId of ["generation-old", "generation-active"]) {
      store.dispatch({
        type: "suggestions_start",
        generation_id: generationId,
        agent_id: "reply_main",
        agent_label: "Main",
        agent_priority: 1,
        suggestion_id: `suggestion-${generationId}`,
        target_utterance_id: "same-turn",
        target_role: "other",
        mode: "normal",
      });
    }

    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-old",
      text: "古い遅延結果",
      final: true,
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-generation-old",
      target_utterance_id: "same-turn",
      target_role: "other",
      mode: "normal",
    });

    const state = useMeetingStore.getState();
    expect(state.activeSuggestionGenerationId).toBe("generation-active");
    expect(state.replyText).toBe("");
    expect(state.isGeneratingReply).toBe(true);
    expect(
      state.suggestionCards.find(
        (card) => card.generationId === "generation-old",
      )?.text,
    ).toBe("古い遅延結果");
  });

  it("discards only the active generation and ignores all later messages for it", () => {
    const store = useMeetingStore.getState();
    store.dispatch({
      type: "suggestions_start",
      generation_id: "generation-discard",
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-discard",
      target_utterance_id: "turn-discard",
      target_role: "other",
      mode: "normal",
    });

    store.discardActiveReply();

    let state = useMeetingStore.getState();
    expect(state.activeSuggestionGenerationId).toBeNull();
    expect(state.activeSuggestionTargetId).toBeNull();
    expect(state.suggestionCards).toEqual([]);
    expect(state.discardedGenerationIds).toContain("generation-discard");

    store.dispatch({
      type: "reply_chunk",
      generation_id: "generation-discard",
      text: "破棄後の遅延結果",
      final: true,
      agent_id: "reply_main",
      agent_label: "Main",
      agent_priority: 1,
      suggestion_id: "suggestion-discard",
      target_utterance_id: "turn-discard",
      target_role: "other",
      mode: "normal",
    });

    state = useMeetingStore.getState();
    expect(state.suggestionCards).toEqual([]);
    expect(state.replyText).toBe("");
  });

  it("bounds live suggestion cards to recent turns during long meetings", () => {
    const store = useMeetingStore.getState();

    for (let i = 0; i < 100; i += 1) {
      const turnId = `turn-${i}`;
      store.dispatch({
        type: "stt_final",
        role: "other",
        text: `Question ${i}`,
        speaker_id: null,
        utterance_id: turnId,
      });

      for (const agent of [
        { id: "reply_main", label: "Main", priority: 1 },
        { id: "reply_polite", label: "Polite", priority: 2 },
      ]) {
        const suggestionId = `${agent.id}-${turnId}`;
        store.dispatch({
          type: "suggestions_start",
          generation_id: `generation-${i}`,
          agent_id: agent.id,
          agent_label: agent.label,
          agent_priority: agent.priority,
          suggestion_id: suggestionId,
          target_utterance_id: turnId,
          target_role: "other",
          mode: "normal",
        });
        store.dispatch({
          type: "reply_chunk",
          generation_id: `generation-${i}`,
          text: `${agent.label} reply ${i}`,
          final: true,
          agent_id: agent.id,
          agent_label: agent.label,
          agent_priority: agent.priority,
          suggestion_id: suggestionId,
          target_utterance_id: turnId,
          target_role: "other",
          mode: "normal",
        });
      }
    }

    const s = useMeetingStore.getState();
    expect(s.suggestionCards.length).toBeLessThanOrEqual(30);
    expect(
      new Set(s.suggestionCards.map((card) => card.targetUtteranceId)),
    ).toEqual(new Set(["turn-95", "turn-96", "turn-97", "turn-98", "turn-99"]));
    expect(s.replyText).toBe("Main reply 99");
    expect(s.isGeneratingReply).toBe(false);
  });

  it("bounds fallback cards inserted by reply chunks without suggestions_start", () => {
    const store = useMeetingStore.getState();

    for (let i = 0; i < 100; i += 1) {
      store.dispatch({
        type: "reply_chunk",
        generation_id: "generation-1",
        text: `Fallback reply ${i}`,
        final: true,
        agent_id: "reply_main",
        agent_label: "Main",
        agent_priority: 1,
        suggestion_id: `fallback-${i}`,
        target_utterance_id: `turn-${i}`,
        target_role: "other",
        mode: "normal",
      });
    }

    expect(
      useMeetingStore.getState().suggestionCards.length,
    ).toBeLessThanOrEqual(30);
  });

  it("dispatches stt_state", () => {
    useMeetingStore.getState().dispatch({
      type: "stt_state",
      backend: "whisper",
      initialized: true,
      initializing: false,
    });
    const s = useMeetingStore.getState();
    expect(s.sttBackend).toBe("whisper");
    expect(s.sttInitialized).toBe(true);
    expect(s.sttInitializing).toBe(false);
  });

  it("setConnected updates state", () => {
    useMeetingStore.getState().setConnected(true);
    expect(useMeetingStore.getState().connected).toBe(true);
  });

  it("reset restores initial state", () => {
    const store = useMeetingStore.getState();
    store.dispatch({ type: "status", text: "X" });
    store.setConnected(true);
    store.reset();

    const s = useMeetingStore.getState();
    expect(s.statusText).toBe("接続中...");
    expect(s.connected).toBe(false);
    expect(s.session).toBeNull();
  });

  // ── sttInitRequested ──────────────────────────────────────────────

  it("setSttInitRequested sets flag", () => {
    useMeetingStore.getState().setSttInitRequested(true);
    expect(useMeetingStore.getState().sttInitRequested).toBe(true);
  });

  it("setSttInitRequested clears flag", () => {
    const store = useMeetingStore.getState();
    store.setSttInitRequested(true);
    store.setSttInitRequested(false);
    expect(useMeetingStore.getState().sttInitRequested).toBe(false);
  });

  it("stt_state clears sttInitRequested", () => {
    const store = useMeetingStore.getState();
    store.setSttInitRequested(true);
    store.dispatch({
      type: "stt_state",
      backend: "whisper",
      initialized: false,
      initializing: true,
    });
    const s = useMeetingStore.getState();
    expect(s.sttInitializing).toBe(true);
    expect(s.sttInitRequested).toBe(false);
  });

  it("error clears pending and backend-confirmed STT loading flags", () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    const store = useMeetingStore.getState();
    store.setSttInitRequested(true);
    useMeetingStore.setState({ sttInitializing: true });

    try {
      store.dispatch({ type: "error", text: "STT init failed" });

      const s = useMeetingStore.getState();
      expect(s.statusText).toBe("エラー: STT init failed");
      expect(s.sttInitializing).toBe(false);
      expect(s.sttInitRequested).toBe(false);
      expect(consoleError).toHaveBeenCalledWith("[WS]", "STT init failed");
    } finally {
      consoleError.mockRestore();
    }
  });
});
