import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  AudioLines,
  Globe,
  LockKeyhole,
  Pause,
  Play,
  RotateCcw,
  Square,
} from "lucide-react";
import { Button } from "../ui/Button";
import "./ConversationSupportPreview.css";

// This development-only concept uses synthetic content and a simulated clock.
// It does not use the meeting store, audio devices, providers, or network APIs.
type ProcessingMode = "local" | "cloud";
type ReplyPhase = "listening" | "thinking" | "ready";
type PreviewView = "conversation" | "prompter";

interface DemoStep {
  afterMs: number;
  phase: ReplyPhase;
  reply: string;
  turnCount: number;
}

const FIRST_SENTENCE = "まずは一つの業務に絞って、試してみませんか。";
const FULL_REPLY = `${FIRST_SENTENCE}どの業務から始めると、効果を確かめやすそうでしょうか。`;

const TURNS = [
  {
    speaker: "相手",
    text: "新しいツールは、一度に全チームへ導入する予定ですか？",
  },
  {
    speaker: "自分",
    text: "まず試してから、広げるかどうか決めたいと思っています。",
  },
  { speaker: "相手", text: "それなら、まず小さく試すことはできますか？" },
  { speaker: "自分", text: FIRST_SENTENCE },
] as const;

const STEPS: DemoStep[] = [
  { afterMs: 0, phase: "listening", reply: "", turnCount: 2 },
  { afterMs: 2500, phase: "thinking", reply: "", turnCount: 3 },
  {
    afterMs: 3600,
    phase: "ready",
    reply: FIRST_SENTENCE,
    turnCount: 3,
  },
  {
    afterMs: 4700,
    phase: "ready",
    reply: FULL_REPLY,
    turnCount: 3,
  },
  {
    afterMs: 8000,
    phase: "ready",
    reply: FULL_REPLY,
    turnCount: 4,
  },
];

export function ConversationSupportPreview() {
  const [mode, setMode] = useState<ProcessingMode>("local");
  const [stepIndex, setStepIndex] = useState(STEPS.length - 1);
  const [playing, setPlaying] = useState(false);
  const [paused, setPaused] = useState(false);
  const [ended, setEnded] = useState(false);
  const [view, setView] = useState<PreviewView>("conversation");
  const compact = view === "prompter";
  const historyRef = useRef<HTMLOListElement>(null);
  const followHistoryRef = useRef(true);
  const step = STEPS[stepIndex];

  useLayoutEffect(() => {
    const history = historyRef.current;
    if (history && followHistoryRef.current) {
      history.scrollTop = history.scrollHeight;
    }
  }, [step.turnCount, view]);

  useEffect(() => {
    if (!playing || paused || ended) return;
    const nextStep = STEPS[stepIndex + 1];
    if (!nextStep) return;
    const timer = window.setTimeout(() => {
      setStepIndex(stepIndex + 1);
      if (stepIndex + 1 === STEPS.length - 1) setPlaying(false);
    }, nextStep.afterMs - step.afterMs);
    return () => window.clearTimeout(timer);
  }, [playing, paused, ended, stepIndex, step.afterMs]);

  function restartDemo() {
    followHistoryRef.current = true;
    setStepIndex(0);
    setPaused(false);
    setEnded(false);
    setPlaying(true);
  }

  const visibleTurns = TURNS.slice(0, step.turnCount);
  const latestOther = [...visibleTurns]
    .reverse()
    .find((turn) => turn.speaker === "相手");
  const status = ended ? "デモ終了" : paused ? "一時停止中" : "自動サポート中";

  return (
    <div className="conversation-preview">
      <aside className="conversation-demo" aria-label="プレビューの操作">
        <div className="conversation-demo__description">
          <span className="conversation-demo__badge">UIプレビュー</span>
          <p>架空の会話です。録音・AI通信は行いません。再生時間は演出です。</p>
        </div>
        <div className="conversation-demo__controls">
          <label>
            会議前の設定
            <select
              className="field"
              value={mode}
              onChange={(event) => {
                const value = event.target.value;
                if (value === "local" || value === "cloud") setMode(value);
              }}
            >
              <option value="local">端末内のみ</option>
              <option value="cloud">クラウド</option>
            </select>
          </label>
          <Button variant="secondary" size="sm" onClick={restartDemo}>
            {playing ? (
              <RotateCcw size={14} aria-hidden="true" />
            ) : (
              <Play size={14} aria-hidden="true" />
            )}
            {playing ? "最初から再生" : "会話デモを再生"}
          </Button>
          <label>
            表示
            <select
              className="field"
              value={view}
              onChange={(event) => {
                const value = event.target.value;
                if (value === "conversation" || value === "prompter")
                  setView(value);
              }}
            >
              <option value="conversation">会話と返答</option>
              <option value="prompter">返答のみ</option>
            </select>
          </label>
        </div>
      </aside>

      <main
        className={`conversation-shell${compact ? " conversation-shell--compact" : ""}`}
      >
        <header className="conversation-header">
          <div className="conversation-brand">
            <AudioLines size={21} aria-hidden="true" />
            <h1>会話サポート</h1>
          </div>
          <div className="conversation-header__actions">
            <Button
              variant="quiet"
              size="icon"
              aria-label={paused ? "サポートを再開" : "サポートを一時停止"}
              title={paused ? "サポートを再開" : "サポートを一時停止"}
              disabled={ended}
              onClick={() => setPaused((value) => !value)}
            >
              {paused ? (
                <Play size={16} aria-hidden="true" />
              ) : (
                <Pause size={16} aria-hidden="true" />
              )}
            </Button>
            <Button
              variant="quiet"
              size="sm"
              disabled={ended}
              onClick={() => {
                setEnded(true);
                setPlaying(false);
              }}
            >
              <Square size={12} aria-hidden="true" />
              終了
            </Button>
          </div>
        </header>

        <div className="conversation-status">
          <span role="status">
            <span
              className={`conversation-status__dot${paused || ended ? " conversation-status__dot--idle" : ""}`}
            />
            {status}
          </span>
          <span
            title={
              mode === "local"
                ? "文字起こしと返答の生成を端末内で行う設定の見本です。"
                : "クラウドLLMを使う設定の見本です。"
            }
          >
            {mode === "local" ? (
              <LockKeyhole size={13} aria-hidden="true" />
            ) : (
              <Globe size={13} aria-hidden="true" />
            )}
            {mode === "local" ? "端末内のみ" : "クラウド利用"}
          </span>
        </div>

        <section
          className="conversation-prompter"
          aria-labelledby="conversation-reply-title"
        >
          <div className="conversation-prompter__heading">
            <h2 id="conversation-reply-title">返答の候補</h2>
            <span>
              {ended
                ? "終了"
                : paused
                  ? "表示を保持中"
                  : step.phase === "thinking"
                    ? "生成中"
                    : step.phase === "listening"
                      ? "聞き取り中"
                      : null}
            </span>
          </div>
          <p className="conversation-prompter__context">
            相手「{latestOther?.text}」
          </p>
          <div
            className="conversation-prompter__body"
            aria-live="polite"
            aria-atomic="true"
          >
            {ended ? (
              <p className="conversation-prompter__placeholder">
                デモを終了しました。
              </p>
            ) : step.reply ? (
              <p>{step.reply}</p>
            ) : (
              <p className="conversation-prompter__placeholder">
                {paused
                  ? "サポートを一時停止しています。"
                  : step.phase === "thinking"
                    ? "返答を生成中…"
                    : "発言を待っています。"}
              </p>
            )}
          </div>
        </section>

        {!compact && (
          <div className="conversation-content">
            <section
              className="conversation-history"
              aria-labelledby="conversation-history-title"
            >
              <div className="conversation-history__heading">
                <h2 id="conversation-history-title">会話</h2>
                <span>{visibleTurns.length}件</span>
              </div>
              <ol
                ref={historyRef}
                className="conversation-history__messages"
                aria-label="会話履歴"
                tabIndex={0}
                onScroll={() => {
                  const history = historyRef.current;
                  if (history)
                    followHistoryRef.current =
                      history.scrollHeight -
                        history.scrollTop -
                        history.clientHeight <=
                      24;
                }}
              >
                {visibleTurns.map((turn, index) => (
                  <li
                    key={index}
                    className={`conversation-message conversation-message--${turn.speaker === "自分" ? "self" : "other"}`}
                  >
                    <span className="conversation-message__speaker">
                      {turn.speaker}
                    </span>
                    <p>{turn.text}</p>
                  </li>
                ))}
              </ol>
            </section>
          </div>
        )}
        {ended && (
          <div className="conversation-ended">
            <p>内容はこのプレビュー内だけの表示です。</p>
            <Button variant="secondary" size="sm" onClick={restartDemo}>
              もう一度試す
            </Button>
          </div>
        )}
      </main>
    </div>
  );
}
