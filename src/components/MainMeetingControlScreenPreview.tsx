import { useState } from "react";
import type { SendFn, SocketState, SuggestionCard } from "../types";
import { MainMeetingControlScreen } from "./MainMeetingControlScreen";

const INITIAL_NOTE = `# 会話メモ

## 決まったこと
- 料金改定の説明資料は1枚にまとめる
- 初稿は来週火曜日までに共有する

## 未確認・懸念
- 既存顧客への適用開始日は未確認

## 次にすること
- 自分：料金改定の背景を2点に整理する
- 相手：適用開始日を法務へ確認する`;

const SUGGESTIONS: SuggestionCard[] = [
  {
    generationId: "preview-generation-1",
    suggestionId: "preview-suggestion-1",
    agentId: "reply-main",
    agentLabel: "標準",
    agentPriority: 1,
    targetUtteranceId: "turn-1",
    targetRole: "other",
    mode: "normal",
    text: "承知しました。顧客向けには、変更理由と影響範囲を1枚で確認できる構成にします。",
    status: "ready",
  },
  {
    generationId: "preview-generation-2",
    suggestionId: "preview-suggestion-2",
    agentId: "reply-main",
    agentLabel: "標準",
    agentPriority: 1,
    targetUtteranceId: "turn-3",
    targetRole: "other",
    mode: "normal",
    text: "ありがとうございます。火曜日までに初稿を共有し、適用開始日は確認結果を反映します。",
    status: "ready",
  },
  {
    generationId: "preview-generation-current",
    suggestionId: "preview-suggestion-current",
    agentId: "reply-main",
    agentLabel: "標準",
    agentPriority: 1,
    targetUtteranceId: "turn-5",
    targetRole: "other",
    mode: "normal",
    text: "承知しました。まず既存顧客への適用日を確認し、その結果を踏まえて告知スケジュールをご提案します。",
    status: "ready",
  },
];

function createPreviewState(): SocketState {
  return {
    connected: true,
    statusText: "接続済み",
    isRunning: true,
    sttBackend: "preview",
    sttInitialized: true,
    sttInitializing: false,
    sttInitRequested: false,
    agentSettings: {
      replyEnabled: true,
      replyAutoGenerate: false,
      replyAgents: [],
      infoEnabled: true,
    },
    devices: [
      { index: 1, name: "会議アプリの音声", is_monitor: true },
      { index: 2, name: "MacBookのマイク", is_monitor: false },
    ],
    deviceOther: 1,
    deviceSelf: 2,
    session: {
      id: "workspace-preview",
      startedAt: new Date(
        Date.now() - 17 * 60 * 1000 - 24 * 1000,
      ).toISOString(),
      title: "料金改定のご案内方針",
      isActive: true,
      turns: [
        {
          id: "turn-1",
          speaker: "other",
          text: "来月の料金改定について、顧客向けの説明資料も用意したいです。",
        },
        {
          id: "turn-2",
          speaker: "self",
          text: "変更理由と影響範囲が短く分かる資料をこちらで作ります。",
        },
        {
          id: "turn-3",
          speaker: "other",
          text: "助かります。来週火曜日までに初稿をいただけますか？",
        },
        {
          id: "turn-4",
          speaker: "self",
          text: "はい、火曜日の午前中までに共有します。",
        },
        {
          id: "turn-5",
          speaker: "other",
          text: "既存のお客様へいつから適用するかも、合わせて確認したいです。",
        },
      ],
      aiNote: INITIAL_NOTE,
    },
    activeSuggestionTargetId: "turn-5",
    activeSuggestionGenerationId: "preview-generation-current",
    suggestionCards: SUGGESTIONS,
    replyText: SUGGESTIONS[2].text,
    isGeneratingReply: false,
    lastReplyCancelResult: null,
    cancelledSuggestionIds: [],
    discardedGenerationIds: [],
    isResearchingInfo: false,
    interimOther: "",
    interimSelf: "",
    levelOther: 0.16,
    levelSelf: 0.08,
  };
}

export function MainMeetingControlScreenPreview() {
  const [state, setState] = useState<SocketState>(createPreviewState);

  const send: SendFn = (message) => {
    if (message.type !== "run_info") return;
    setState((current) => ({ ...current, isResearchingInfo: true }));
    window.setTimeout(() => {
      setState((current) => ({ ...current, isResearchingInfo: false }));
    }, 900);
  };

  return (
    <div className="flex h-screen min-w-[720px] flex-col overflow-hidden bg-paper">
      <MainMeetingControlScreen
        state={state}
        send={send}
        onSettings={() => undefined}
        replyReadiness="ready"
        infoRouteStatus={{
          readiness: "ready",
          canGenerate: true,
          message: null,
        }}
      />
    </div>
  );
}
