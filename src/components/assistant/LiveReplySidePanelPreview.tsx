import { useMemo, useState } from "react";
import { StandaloneLiveReplyPanel } from "./LiveReplySidePanel";
import type {
  SendFn,
  SocketState,
  SuggestionCard,
  Turn,
  WsMessage,
} from "../../types";

type PreviewScenarioId =
  | "idle"
  | "generating"
  | "generated"
  | "failed"
  | "reply-off"
  | "long-history";

interface PreviewScenario {
  id: PreviewScenarioId;
  label: string;
  description: string;
  state: SocketState;
}

interface PreviewLog {
  kind: "send" | "close" | "clipboard";
  message: string;
}

const PREVIEW_PANEL_HEIGHT_CLASS = "h-[min(700px,calc(100vh-6rem))]";

const DEFAULT_AGENT_SETTINGS: SocketState["agentSettings"] = {
  replyEnabled: true,
  replyAutoGenerate: false,
  replyAgents: [],
  infoEnabled: true,
};

const BASE_TURNS: Turn[] = [
  {
    id: "turn-1",
    speaker: "other",
    text: "来週のリリースで、顧客向けの説明資料も必要そうです。",
  },
  {
    id: "turn-2",
    speaker: "self",
    text: "資料のたたき台はこちらで用意します。",
  },
  {
    id: "turn-3",
    speaker: "other",
    text: "ありがとうございます。料金改定の背景も短く入れたいです。",
  },
];

const GENERATED_REPLY =
  "承知しました。料金改定の背景は、提供価値の拡大とサポート体制強化の2点に絞って、1枚で説明できる形にまとめます。";

function createBaseState(overrides: Partial<SocketState> = {}): SocketState {
  return {
    connected: true,
    statusText: "プレビュー接続中",
    isRunning: true,
    sttBackend: "preview",
    sttInitialized: true,
    sttInitializing: false,
    sttInitRequested: false,
    agentSettings: DEFAULT_AGENT_SETTINGS,
    devices: [],
    deviceOther: null,
    deviceSelf: null,
    session: {
      id: "preview-session",
      startedAt: "2026-07-06T00:00:00.000Z",
      title: "LiveReplySidePanel preview",
      isActive: true,
      turns: BASE_TURNS,
      aiNote: "",
    },
    activeSuggestionTargetId: "turn-3",
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
    ...overrides,
  };
}

function createSuggestionCard(text: string): SuggestionCard {
  return {
    generationId: "preview-generation-1",
    suggestionId: "preview-suggestion-1",
    agentId: "reply-agent-preview",
    agentLabel: "返答支援プレビュー",
    agentPriority: 1,
    targetUtteranceId: "turn-3",
    targetRole: "other",
    mode: "normal",
    text,
    status: "ready",
  };
}

function createLongTurns(): Turn[] {
  return Array.from(
    { length: 18 },
    (_, index): Turn => ({
      id: `long-turn-${index + 1}`,
      speaker: index % 2 === 0 ? "other" : "self",
      text:
        index % 2 === 0
          ? `論点 ${index + 1}: 顧客への伝え方とスケジュールをすり合わせたいです。`
          : `対応 ${index + 1}: 影響範囲を整理して、次回までに確認します。`,
    }),
  );
}

function createScenarios(): PreviewScenario[] {
  return [
    {
      id: "idle",
      label: "待機中",
      description: "返答案がまだなく、手動生成を促す状態です。",
      state: createBaseState({
        isRunning: false,
        session: null,
        activeSuggestionTargetId: null,
      }),
    },
    {
      id: "generating",
      label: "生成中",
      description: "会話履歴をもとに返答案を生成している状態です。",
      state: createBaseState({
        isGeneratingReply: true,
        interimOther: "この変更はいつから適用される想定でしょうか？",
      }),
    },
    {
      id: "failed",
      label: "生成失敗",
      description: "Codex との通信が切れ、再試行できる状態です。",
      state: createBaseState({
        activeSuggestionGenerationId: "preview-generation-1",
        suggestionCards: [
          {
            ...createSuggestionCard(""),
            status: "error",
            errorText:
              "Codex との通信が途中で切れました。接続を確認してもう一度お試しください。",
          },
        ],
      }),
    },
    {
      id: "generated",
      label: "生成後",
      description: "生成済みの返答案とコピー操作を確認できます。",
      state: createBaseState({
        replyText: GENERATED_REPLY,
        suggestionCards: [createSuggestionCard(GENERATED_REPLY)],
      }),
    },
    {
      id: "reply-off",
      label: "返答OFF",
      description: "返答支援が設定で OFF の状態です。",
      state: createBaseState({
        agentSettings: {
          ...DEFAULT_AGENT_SETTINGS,
          replyEnabled: false,
        },
      }),
    },
    {
      id: "long-history",
      label: "長い履歴",
      description:
        "会話履歴が多い場合のスクロールと下部操作の見え方を確認できます。",
      state: createBaseState({
        session: {
          id: "preview-long-session",
          startedAt: "2026-07-06T00:00:00.000Z",
          title: "Long history preview",
          isActive: true,
          turns: createLongTurns(),
          aiNote: "",
        },
        activeSuggestionTargetId: "long-turn-17",
        replyText:
          "まず影響範囲を整理し、適用時期と顧客告知のタイミングを分けて確認させてください。",
      }),
    },
  ];
}

function formatMessage(message: WsMessage): string {
  return JSON.stringify(message);
}

export function LiveReplySidePanelPreview() {
  const scenarios = useMemo(createScenarios, []);
  const [scenarioId, setScenarioId] = useState<PreviewScenarioId>("generated");
  const [lastLog, setLastLog] = useState<PreviewLog | null>(null);
  const selectedScenario =
    scenarios.find((scenario) => scenario.id === scenarioId) ?? scenarios[0];

  const send: SendFn = (message) => {
    setLastLog({ kind: "send", message: formatMessage(message) });
  };

  async function writeClipboard(text: string): Promise<void> {
    setLastLog({ kind: "clipboard", message: text });
  }

  function closePreviewPanel(): void {
    setLastLog({
      kind: "close",
      message: "プレビューでは閉じずに onClose を記録しました。",
    });
  }

  return (
    <div className="min-h-screen bg-slate-950 px-5 py-6 text-slate-100">
      <div className="mx-auto flex max-w-5xl flex-col gap-5 lg:flex-row lg:items-start">
        <aside className="w-full rounded-3xl border border-white/10 bg-white/10 p-4 shadow-2xl backdrop-blur lg:w-72">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-blue-200">
            dev preview
          </p>
          <h1 className="mt-2 text-xl font-bold">LiveReplySidePanel</h1>
          <p className="mt-2 text-sm leading-relaxed text-slate-300">
            backend / WebSocket / Tauri window なしで、実際の React
            コンポーネントを確認するための開発用プレビューです。
          </p>

          <div className="mt-5 grid grid-cols-2 gap-2 lg:grid-cols-1">
            {scenarios.map((scenario) => {
              const isActive = scenario.id === scenarioId;
              return (
                <button
                  key={scenario.id}
                  type="button"
                  aria-pressed={isActive}
                  onClick={() => setScenarioId(scenario.id)}
                  className={`rounded-2xl border px-3 py-2 text-left text-sm transition ${
                    isActive
                      ? "border-blue-300 bg-blue-500 text-white shadow-lg shadow-blue-950/30"
                      : "border-white/10 bg-white/5 text-slate-200 hover:border-blue-200/60 hover:bg-white/10"
                  }`}
                >
                  <span className="font-semibold">{scenario.label}</span>
                  <span
                    className={`mt-1 block text-xs leading-relaxed ${isActive ? "text-blue-50" : "text-slate-400"}`}
                  >
                    {scenario.description}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="mt-5 rounded-2xl border border-white/10 bg-slate-900/70 p-3 text-xs text-slate-300">
            <div className="font-semibold text-slate-100">最後の操作</div>
            {lastLog ? (
              <dl className="mt-2 space-y-1">
                <div>
                  <dt className="text-slate-500">種別</dt>
                  <dd>{lastLog.kind}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">内容</dt>
                  <dd className="break-words font-mono text-[11px] text-blue-100">
                    {lastLog.message}
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="mt-2 text-slate-500">まだ操作はありません。</p>
            )}
          </div>
        </aside>

        <main className="flex w-full justify-center lg:justify-start">
          <div
            className={`w-full max-w-[420px] overflow-hidden rounded-[28px] border border-white/15 bg-white shadow-2xl ring-1 ring-black/20 sm:w-[390px] ${PREVIEW_PANEL_HEIGHT_CLASS}`}
          >
            <StandaloneLiveReplyPanel
              state={selectedScenario.state}
              send={send}
              onClose={closePreviewPanel}
              writeClipboard={writeClipboard}
              panelHeightClass="h-full"
              replyReadiness="ready"
            />
          </div>
        </main>
      </div>
    </div>
  );
}
