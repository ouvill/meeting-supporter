import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Check,
  Clipboard,
  LoaderCircle,
  MessageSquareQuote,
  RefreshCw,
  Settings,
  Sparkles,
  X,
} from "lucide-react";
import type {
  SendFn,
  SocketState,
  SuggestionCard,
  SuggestionMode,
  Turn,
} from "../../types";
import { hideCurrentWindow } from "../../platform/tauriWindow";
import {
  useAiRoutes,
  type AiRouteReadiness,
  type AiUseCaseRouteStatus,
} from "../../hooks/useAiRoutes";
import {
  ASSISTANT_ALWAYS_ON_TOP_KEY,
  useAlwaysOnTop,
  type AlwaysOnTopController,
} from "../../hooks/useAlwaysOnTop";
import { AlwaysOnTopControl } from "../window/AlwaysOnTopControl";
import { Button, InlineNotice } from "../ui";

export type ReplyReadiness = AiRouteReadiness | "unknown";

interface Props {
  state: SocketState;
  send: SendFn;
  onClose?: () => void;
  writeClipboard?: (text: string) => Promise<void>;
  panelHeightClass?: string;
  replyReadiness?: ReplyReadiness;
  onDiscardReply?: () => void;
}

interface LiveReplySurfaceProps extends Props {
  embedded: boolean;
  alwaysOnTop?: AlwaysOnTopController;
  replyRouteStatus?: AiUseCaseRouteStatus;
}

type CopyState = "idle" | "copied" | "failed";
type CancelFeedback = { tone: "positive" | "danger"; message: string } | null;

const MODE_CHIPS: Array<{ label: string; mode: SuggestionMode }> = [
  { label: "短く", mode: "short" },
  { label: "丁寧に", mode: "polite" },
  { label: "質問で", mode: "clarify" },
  { label: "時間をもらう", mode: "buy_time" },
];
export const CANCEL_RESULT_TIMEOUT_MS = 10_000;

function findPrimarySuggestion(
  cards: SuggestionCard[],
  generationId: string | null,
  targetId: string | null,
): SuggestionCard | null {
  if (!generationId) return null;
  return (
    [...cards]
      .sort((left, right) => {
        if (left.agentPriority !== right.agentPriority)
          return left.agentPriority - right.agentPriority;
        return left.agentLabel.localeCompare(right.agentLabel);
      })
      .find(
        (card) =>
          card.generationId === generationId &&
          (!targetId || card.targetUtteranceId === targetId) &&
          (card.status === "generating" || card.status === "ready") &&
          card.text,
      ) ?? null
  );
}

function hasSuggestionError(
  cards: SuggestionCard[],
  generationId: string | null,
  targetId: string | null,
): boolean {
  if (!generationId) return false;
  return cards.some(
    (card) =>
      card.generationId === generationId &&
      (!targetId || card.targetUtteranceId === targetId) &&
      card.status === "error",
  );
}

async function writeToNavigatorClipboard(text: string): Promise<void> {
  const clipboard = navigator.clipboard;
  if (!clipboard) throw new Error("Clipboard API is unavailable");
  await clipboard.writeText(text);
}

function readinessMessage(readiness: ReplyReadiness, enabled: boolean): string {
  if (!enabled) return "返答支援はオフになっています。";
  if (readiness === "setup_required")
    return "返答支援を使うには準備が必要です。";
  if (readiness === "not_offered" || readiness === "unavailable")
    return "現在、返答支援を利用できません。";
  if (readiness === "error" || readiness === "unknown")
    return "返答支援の準備状況を確認できません。";
  return "";
}

export function LiveReplySidePanel(props: Props) {
  const alwaysOnTop = useAlwaysOnTop({
    defaultDesired: true,
    storageKey: ASSISTANT_ALWAYS_ON_TOP_KEY,
  });
  const replyRouteStatus = useAiRoutes().replyStatus;

  return (
    <LiveReplySurface
      {...props}
      embedded={false}
      alwaysOnTop={alwaysOnTop}
      replyRouteStatus={replyRouteStatus}
    />
  );
}

export function EmbeddedLiveReplyPanel(props: Props) {
  return <LiveReplySurface {...props} embedded />;
}

function LiveReplySurface({
  state,
  send,
  onClose,
  writeClipboard = writeToNavigatorClipboard,
  onDiscardReply,
  panelHeightClass = "h-screen",
  replyReadiness,
  embedded,
  alwaysOnTop,
  replyRouteStatus,
}: LiveReplySurfaceProps) {
  const resolvedReadiness: ReplyReadiness =
    replyReadiness ?? replyRouteStatus?.readiness ?? "unknown";
  const replyRouteReady =
    replyReadiness === undefined
      ? (replyRouteStatus?.canGenerate ?? false)
      : replyReadiness === "ready";
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const [cancelRequestGenerationId, setCancelRequestGenerationId] = useState<
    string | null
  >(null);
  const [cancelFeedback, setCancelFeedback] = useState<CancelFeedback>(null);
  const turns = state.session?.turns ?? [];
  const latestFinalTurn = turns.length > 0 ? turns[turns.length - 1] : null;
  const nextGenerationTargetId =
    latestFinalTurn?.id ?? state.activeSuggestionTargetId ?? null;
  const displayedSuggestionTargetId =
    state.activeSuggestionTargetId ?? nextGenerationTargetId;
  const primarySuggestion = useMemo(
    () =>
      findPrimarySuggestion(
        state.suggestionCards,
        state.activeSuggestionGenerationId,
        displayedSuggestionTargetId,
      ),
    [
      state.suggestionCards,
      state.activeSuggestionGenerationId,
      displayedSuggestionTargetId,
    ],
  );
  const suggestionFailed = useMemo(
    () =>
      hasSuggestionError(
        state.suggestionCards,
        state.activeSuggestionGenerationId,
        displayedSuggestionTargetId,
      ),
    [
      state.suggestionCards,
      state.activeSuggestionGenerationId,
      displayedSuggestionTargetId,
    ],
  );
  const replyText = state.replyText || primarySuggestion?.text || "";
  const replyEnabled = state.agentSettings.replyEnabled;
  const canGenerateReply =
    state.connected &&
    state.isRunning &&
    replyEnabled &&
    replyRouteReady &&
    !state.isGeneratingReply &&
    !cancelRequestGenerationId;
  const latestLiveTurn: {
    speaker: Turn["speaker"];
    text: string;
    interim: boolean;
  } | null = state.interimOther
    ? { speaker: "other", text: state.interimOther, interim: true }
    : state.interimSelf
      ? { speaker: "self", text: state.interimSelf, interim: true }
      : latestFinalTurn
        ? {
            speaker: latestFinalTurn.speaker,
            text: latestFinalTurn.text,
            interim: false,
          }
        : null;

  useEffect(() => {
    if (copyState === "idle") return undefined;
    const timerId = window.setTimeout(() => setCopyState("idle"), 1600);
    return () => window.clearTimeout(timerId);
  }, [copyState]);

  useEffect(() => {
    const result = state.lastReplyCancelResult;
    if (
      !cancelRequestGenerationId ||
      !result ||
      result.generationId !== cancelRequestGenerationId
    )
      return;
    setCancelRequestGenerationId(null);
    setCancelFeedback(
      result.status === "applied"
        ? { tone: "positive", message: "返答案の生成を停止しました。" }
        : {
            tone: "danger",
            message: "停止対象が見つからないか、返答案がすでに完了しています。",
          },
    );
  }, [cancelRequestGenerationId, state.lastReplyCancelResult]);

  useEffect(() => {
    if (!cancelRequestGenerationId) return undefined;
    if (!state.connected) {
      setCancelRequestGenerationId(null);
      setCancelFeedback({
        tone: "danger",
        message:
          "停止結果を確認できませんでした。接続を確認してもう一度お試しください。",
      });
      return undefined;
    }
    const timerId = window.setTimeout(() => {
      setCancelRequestGenerationId(null);
      setCancelFeedback({
        tone: "danger",
        message:
          "停止結果を確認できませんでした。接続を確認してもう一度お試しください。",
      });
    }, CANCEL_RESULT_TIMEOUT_MS);
    return () => window.clearTimeout(timerId);
  }, [cancelRequestGenerationId, state.connected]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (
        !(event.ctrlKey || event.metaKey) ||
        event.key !== "Enter" ||
        event.repeat ||
        !canGenerateReply
      )
        return;
      const target = event.target;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        (target instanceof HTMLElement && target.isContentEditable)
      )
        return;
      event.preventDefault();
      requestReply();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  function requestReply(mode?: SuggestionMode) {
    if (!canGenerateReply) return;
    const generationId = crypto.randomUUID();
    send(
      nextGenerationTargetId
        ? {
            type: "generate_reply",
            generation_id: generationId,
            target_utterance_id: nextGenerationTargetId,
            ...(mode ? { mode } : {}),
          }
        : {
            type: "generate_reply",
            generation_id: generationId,
            ...(mode ? { mode } : {}),
          },
    );
  }

  function cancelReply() {
    const generationId = state.activeSuggestionGenerationId;
    const targetUtteranceId = state.activeSuggestionTargetId;
    if (
      !state.connected ||
      !generationId ||
      !targetUtteranceId ||
      cancelRequestGenerationId
    )
      return;
    setCancelRequestGenerationId(generationId);
    setCancelFeedback(null);
    send({
      type: "cancel_reply",
      generation_id: generationId,
      target_utterance_id: targetUtteranceId,
    });
  }

  async function copyReply() {
    if (!replyText) return;
    try {
      await writeClipboard(replyText);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  }

  function closePanel() {
    if (onClose) {
      onClose();
      return;
    }
    void hideCurrentWindow();
  }

  const ContentRoot = embedded ? "div" : "main";

  return (
    <div
      data-testid="live-reply-panel"
      className={`${embedded ? "h-full min-h-0" : `${panelHeightClass} min-h-[560px]`} w-full overflow-hidden bg-paper text-ink flex flex-col`}
    >
      {!embedded && (
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-3.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary text-white shadow-sm">
            <Sparkles aria-hidden="true" size={16} />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="truncate font-display text-base font-bold tracking-[0.01em] text-ink">
              会話プロンプター
            </h1>
            <p
              className="flex items-center gap-1.5 text-xs font-medium text-ink-muted"
              role="status"
              aria-live="polite"
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${state.connected && state.isRunning ? "bg-positive" : "bg-warning"}`}
                aria-hidden="true"
              />
              {state.connected
                ? state.isRunning
                  ? "会議中"
                  : "待機中"
                : "接続を確認中"}
            </p>
          </div>
          {alwaysOnTop && <AlwaysOnTopControl controller={alwaysOnTop} />}
        </header>
      )}

      <ContentRoot
        className={`flex min-h-0 flex-1 flex-col overflow-hidden ${embedded ? "gap-3 p-0" : "gap-2.5 p-3"}`}
      >
        {!embedded && (
          <section
            className="shrink-0"
            aria-labelledby="latest-utterance-heading"
          >
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <h2
                id="latest-utterance-heading"
                className="text-xs font-bold tracking-[0.12em] text-ink-muted"
              >
                直前の発言
              </h2>
              {latestLiveTurn && (
                <span
                  className={`text-xs font-bold ${latestLiveTurn.speaker === "other" ? "text-cue" : "text-positive"}`}
                >
                  {latestLiveTurn.speaker === "other" ? "相手" : "自分"}
                  {latestLiveTurn.interim ? "・聞き取り中" : ""}
                </span>
              )}
            </div>
            <div
              className="flex min-h-16 max-h-20 items-center overflow-y-auto rounded-2xl border border-line bg-surface px-3.5 py-2.5 shadow-sm"
              aria-live="polite"
              tabIndex={0}
            >
              <p
                className={`text-sm leading-6 ${latestLiveTurn ? "text-ink" : "text-ink-muted"}`}
              >
                {latestLiveTurn?.text || "発言を待っています"}
              </p>
            </div>
          </section>
        )}

        <section
          className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-cue/30 bg-surface shadow-raised"
          aria-labelledby="cue-card-heading"
        >
          <div className="flex shrink-0 items-center justify-between border-b border-cue/20 bg-cue-soft px-3.5 py-2">
            <div className="flex items-center gap-2">
              <MessageSquareQuote
                aria-hidden="true"
                size={15}
                className="text-cue"
              />
              <h2
                id="cue-card-heading"
                className="font-display text-sm font-bold tracking-[0.08em] text-cue"
              >
                {embedded ? "現在の返答案" : "次に話すこと"}
              </h2>
            </div>
            {state.isGeneratingReply && (
              <span
                className="flex items-center gap-1 text-xs font-bold text-cue"
                aria-live="polite"
              >
                <LoaderCircle
                  aria-hidden="true"
                  size={12}
                  className="animate-spin motion-reduce:animate-none"
                />
                作成中
              </span>
            )}
          </div>
          <div
            className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3"
            aria-live="polite"
            aria-atomic="true"
            tabIndex={0}
          >
            {replyText ? (
              <p
                className={`whitespace-pre-wrap font-semibold text-ink ${
                  embedded ? "text-base leading-7" : "text-xl leading-8"
                }`}
              >
                {replyText}
              </p>
            ) : state.isGeneratingReply ? (
              <div className="space-y-2.5 py-1" aria-label="返答案を作成中">
                <div className="h-3 w-11/12 animate-pulse rounded bg-cue-soft motion-reduce:animate-none" />
                <div className="h-3 w-4/5 animate-pulse rounded bg-cue-soft motion-reduce:animate-none" />
                <div className="h-3 w-2/3 animate-pulse rounded bg-cue-soft motion-reduce:animate-none" />
              </div>
            ) : replyRouteReady && replyEnabled ? (
              <p className="text-sm leading-6 text-ink-muted">
                必要なときに「返答案を作る」を押してください。
              </p>
            ) : (
              <div className="flex h-full min-h-16 items-center gap-2.5 text-ink-muted">
                <AlertCircle
                  aria-hidden="true"
                  size={17}
                  className="shrink-0"
                />
                <p className="text-sm font-medium leading-6">
                  {readinessMessage(
                    resolvedReadiness === "ready" && !replyRouteReady
                      ? "unavailable"
                      : resolvedReadiness,
                    replyEnabled,
                  )}
                </p>
              </div>
            )}
          </div>
        </section>

        {suggestionFailed && (
          <InlineNotice
            tone="danger"
            title="返答案を作れませんでした"
            className="shrink-0 px-3 py-2 text-sm"
            aria-live="assertive"
            action={
              <Button
                variant="danger"
                size="sm"
                onClick={() => requestReply()}
                disabled={!canGenerateReply}
                className="min-h-8 px-2 text-xs"
              >
                <RefreshCw aria-hidden="true" size={11} />
                再試行
              </Button>
            }
          >
            <p className="text-xs leading-5">
              内容は消えていません。もう一度試すか、メイン画面で設定を確認してください。
            </p>
          </InlineNotice>
        )}

        {cancelFeedback && (
          <InlineNotice
            tone={cancelFeedback.tone}
            title={cancelFeedback.message}
            className="shrink-0 px-3 py-2 text-sm"
            aria-live={
              cancelFeedback.tone === "danger" ? "assertive" : "polite"
            }
          >
            <span className="sr-only">停止操作の結果です。</span>
          </InlineNotice>
        )}

        <section className="shrink-0" aria-label="返答操作">
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <Button
              variant="cue"
              size="md"
              onClick={
                state.isGeneratingReply ? cancelReply : () => requestReply()
              }
              disabled={
                cancelRequestGenerationId !== null ||
                (state.isGeneratingReply
                  ? !state.connected ||
                    !state.activeSuggestionGenerationId ||
                    !state.activeSuggestionTargetId
                  : !canGenerateReply)
              }
              className="w-full rounded-xl text-sm motion-reduce:transform-none motion-reduce:transition-none"
            >
              {state.isGeneratingReply ? (
                <X aria-hidden="true" size={15} />
              ) : (
                <Sparkles aria-hidden="true" size={15} />
              )}
              {cancelRequestGenerationId
                ? "停止結果を確認中"
                : state.isGeneratingReply
                  ? "停止"
                  : "返答案を作る"}
            </Button>
            <Button
              variant="secondary"
              size="md"
              onClick={() => void copyReply()}
              disabled={!replyText}
              aria-label={
                copyState === "copied"
                  ? "コピーしました"
                  : copyState === "failed"
                    ? "コピー失敗"
                    : "コピー"
              }
              className="h-10 min-w-20 rounded-xl px-3 text-sm"
            >
              {copyState === "copied" ? (
                <Check aria-hidden="true" size={14} />
              ) : (
                <Clipboard aria-hidden="true" size={14} />
              )}
              {copyState === "copied"
                ? "コピーしました"
                : copyState === "failed"
                  ? "失敗"
                  : "コピー"}
            </Button>
          </div>

          <div
            className="mt-2 flex items-center gap-1.5 overflow-x-auto pb-0.5"
            aria-label="返答案を言い換え"
            tabIndex={0}
          >
            <span className="shrink-0 text-xs font-bold text-ink-muted">
              言い換える
            </span>
            {MODE_CHIPS.map((item) => (
              <button
                key={item.mode}
                type="button"
                onClick={() => requestReply(item.mode)}
                disabled={!canGenerateReply}
                className="shrink-0 rounded-full border border-line bg-surface px-2.5 py-1 text-xs font-semibold text-ink-muted transition-colors hover:border-primary/45 hover:text-primary disabled:cursor-not-allowed disabled:text-ink-faint motion-reduce:transition-none"
              >
                {item.label}
              </button>
            ))}
            {replyText && !state.isGeneratingReply && onDiscardReply && (
              <button
                type="button"
                onClick={onDiscardReply}
                className="ml-auto shrink-0 rounded-full border border-line bg-surface px-2.5 py-1 text-xs font-semibold text-ink-muted transition-colors hover:border-danger/45 hover:text-danger motion-reduce:transition-none"
              >
                破棄
              </button>
            )}
          </div>

          {!replyRouteReady && (
            <Button
              variant="quiet"
              size="sm"
              onClick={closePanel}
              className="mt-1.5 min-h-8 w-full py-1 text-xs text-ink-muted"
            >
              <Settings aria-hidden="true" size={12} />
              メイン画面で設定を確認
            </Button>
          )}
        </section>
      </ContentRoot>
    </div>
  );
}
