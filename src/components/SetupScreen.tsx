import { useId, useState } from "react";
import type {
  MeetingContextInput,
  ReferenceDocumentInput,
  SendFn,
  SocketState,
} from "../types";
import type {
  AiRoutesReloadStatus,
  AiUseCaseRouteStatus,
} from "../hooks/useAiRoutes";
import { useMeetingStart } from "../hooks/useMeetingStart";
import type { SavedSpeechReadiness } from "../hooks/useSavedSpeechReadiness";
import { AudioInputs } from "./setup/AudioInputs";
import { ReferenceDocuments } from "./setup/ReferenceDocuments";
import { contextWithFallback } from "./setup/setupUtils";
import { Button, InlineNotice, StickyActionBar } from "./ui";

interface Props {
  state: SocketState;
  send: SendFn;
  showFirstRunGuidance: boolean;
  onSettings: () => void;
  onSpeechSettings?: () => void;
  onHistory?: () => void;
  replyStatus: AiUseCaseRouteStatus;
  replyReloadStatus: AiRoutesReloadStatus;
  onReloadReplyStatus: () => void;
  speechReadiness?: SavedSpeechReadiness;
}
const DEFAULT_MEETING_CONTEXT: MeetingContextInput = {
  scenario: "",
  userRole: "",
  counterpartRole: "",
  objective: "",
  background: "",
  tone: "",
  constraints: "",
  customInstructions: "",
};
const SPEECH_LABELS: Record<SavedSpeechReadiness, string> = {
  checking: "モデルを確認しています…",
  ready: "準備済み・この端末で処理",
  missing: "初回のモデルダウンロードが必要です",
  downloading: "モデルをダウンロードしています…",
  error: "モデルの準備状態を確認できません",
};

export function SetupScreen({
  state,
  send,
  showFirstRunGuidance,
  onSettings,
  onSpeechSettings = onSettings,
  onHistory,
  replyStatus,
  replyReloadStatus,
  onReloadReplyStatus,
  speechReadiness,
}: Props) {
  const [meetingContext, setMeetingContext] = useState<MeetingContextInput>(
    DEFAULT_MEETING_CONTEXT,
  );
  const [references, setReferences] = useState<ReferenceDocumentInput[]>([]);
  const start = useMeetingStart(state, send);
  const busy = start.phase !== "idle";
  const stopFailed = state.meetingEndStatus === "stop_failed";
  const audioReady =
    state.sttInitialized ||
    speechReadiness === undefined ||
    speechReadiness === "ready";
  const canStart =
    state.connected &&
    audioReady &&
    !stopFailed &&
    !busy &&
    !state.sttInitializing &&
    !state.sttInitRequested;
  const replyReady =
    state.agentSettings.replyEnabled && replyStatus.canGenerate;
  const replyMessage = !state.agentSettings.replyEnabled
    ? "オフ・文字起こしのみ利用"
    : replyReady
      ? "利用可能"
      : (replyStatus.message ?? "AIの準備を確認しています…");
  function updateContext<K extends keyof MeetingContextInput>(
    key: K,
    value: MeetingContextInput[K],
  ) {
    setMeetingContext((current) => ({ ...current, [key]: value }));
  }
  function startMeeting() {
    if (!canStart) return;
    start.start({
      type: "start_meeting",
      meeting_context: contextWithFallback(meetingContext),
      references: references.filter((document) => document.status !== "failed"),
    });
  }
  return (
    <div
      data-testid="setup-screen"
      className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-surface text-ink"
    >
      <main className="mx-auto w-full max-w-3xl flex-1 px-6 pb-6 pt-6 sm:px-10 sm:pt-7">
        {state.meetingEndStatus && state.meetingEndStatus !== "completed" && (
          <InlineNotice
            className="mb-5"
            tone={
              state.meetingEndStatus === "interrupted" ? "warning" : "danger"
            }
            title={
              stopFailed
                ? "音声処理の停止を確認できませんでした"
                : state.meetingEndStatus === "unsaved"
                  ? "一部の記録を保存できませんでした"
                  : "会議を中断しました"
            }
            action={
              onHistory ? (
                <Button variant="secondary" onClick={onHistory}>
                  履歴を確認
                </Button>
              ) : undefined
            }
          >
            {stopFailed
              ? "アプリを再起動してください。保存済みの記録は履歴から確認できます。"
              : state.meetingEndStatus === "unsaved"
                ? "保存先の空き容量と状態を確認してください。保存済みの記録は履歴から確認できます。"
                : "保存できた記録は履歴から確認できます。文字起こしや録音が欠けている可能性があります。"}
          </InlineNotice>
        )}

        <header className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight">新しい会議</h1>
          <p className="mt-2 text-sm leading-6 text-ink-muted">
            {showFirstRunGuidance
              ? "音声を確認して、会議を始めましょう。会議の情報はあとからでも大丈夫です。"
              : "音声を確認したら、そのまま開始できます。"}
          </p>
        </header>
        <section aria-labelledby="audio-check-heading">
          <h2 id="audio-check-heading" className="text-base font-semibold">
            音声チェック
          </h2>
          <p className="mt-1 text-sm text-ink-muted">
            話すか音を流して、音量バーが動くことを確認してください。
          </p>
          <AudioInputs state={state} send={send} locked={busy} />
        </section>
        <section
          aria-label="利用する機能"
          className="my-4 border-y border-line"
        >
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 py-3 text-sm">
            <span className="w-20 font-medium">文字起こし</span>
            <span role="status" className="min-w-0 flex-1 text-ink-muted">
              {start.phase === "cancelling"
                ? "準備を取り消しています…"
                : start.phase === "preparing"
                  ? "音声認識を準備しています…"
                  : state.sttInitialized
                    ? SPEECH_LABELS.ready
                    : SPEECH_LABELS[speechReadiness ?? "ready"]}
            </span>
            <Button
              variant="quiet"
              size="sm"
              onClick={onSpeechSettings}
              disabled={busy}
            >
              {speechReadiness === "missing" ? "モデルを準備" : "設定"}
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 py-3 text-sm">
            <span className="w-20 font-medium">返答案</span>
            <span role="status" className="min-w-0 flex-1 text-ink-muted">
              {replyMessage}
            </span>
            <Button
              variant="quiet"
              size="sm"
              onClick={onSettings}
              disabled={busy}
            >
              AIを設定
            </Button>
            {state.agentSettings.replyEnabled &&
              ["error", "unavailable"].includes(replyStatus.readiness) && (
                <Button
                  variant="quiet"
                  size="sm"
                  onClick={onReloadReplyStatus}
                  disabled={replyReloadStatus === "loading"}
                >
                  {replyReloadStatus === "loading" ? "確認中…" : "再確認"}
                </Button>
              )}
          </div>
          {!replyReady && (
            <p className="pb-3 text-xs text-ink-muted">
              返答案を利用できない場合も、録音と文字起こしは開始できます。
            </p>
          )}
        </section>
        <fieldset disabled={busy} className="min-w-0 space-y-5 border-0 p-0">
          <label className="block">
            <span className="mb-2 block text-sm font-medium">
              会議の目的{" "}
              <span className="ml-1 text-xs font-normal text-ink-muted">
                任意
              </span>
            </span>
            <textarea
              aria-label="今日持ち帰りたいこと"
              value={meetingContext.objective}
              onChange={(event) =>
                updateContext("objective", event.target.value)
              }
              placeholder="例：次回までの担当と期限を決めたい"
              rows={2}
              className="field resize-y text-sm"
            />
          </label>
          <details className="border-b border-line pb-5">
            <summary className="cursor-pointer py-2 text-sm font-medium">
              会議の詳細・資料を追加
            </summary>
            <p className="my-3 text-xs text-ink-muted">
              すべて任意です。わかる範囲で入力してください。
            </p>
            <div className="space-y-5 pt-1">
              <div className="grid gap-5 sm:grid-cols-2">
                <Field
                  label="会議の種類"
                  value={meetingContext.scenario}
                  placeholder="例：商談、面接、1on1"
                  onChange={(value) => updateContext("scenario", value)}
                />
                <Field
                  label="あなたの立場"
                  value={meetingContext.userRole}
                  placeholder="例：進行役、提案する側"
                  onChange={(value) => updateContext("userRole", value)}
                />
                <Field
                  label="相手の立場"
                  value={meetingContext.counterpartRole ?? ""}
                  placeholder="例：取引先の担当者"
                  onChange={(value) => updateContext("counterpartRole", value)}
                />
                <Field
                  label="希望する話し方"
                  value={meetingContext.tone ?? ""}
                  placeholder="例：率直に、やわらかく"
                  onChange={(value) => updateContext("tone", value)}
                />
              </div>
              <Area
                label="これまでの経緯"
                value={meetingContext.background ?? ""}
                placeholder="共有しておきたい背景や前提"
                onChange={(value) => updateContext("background", value)}
              />
              <Field
                label="避けたいこと"
                value={meetingContext.constraints ?? ""}
                placeholder="触れない話題や守る条件"
                onChange={(value) => updateContext("constraints", value)}
              />
              <Field
                label="そのほかの希望"
                value={meetingContext.customInstructions ?? ""}
                placeholder="特に意識してほしいこと"
                onChange={(value) => updateContext("customInstructions", value)}
              />
              <ReferenceDocuments
                references={references}
                onChange={setReferences}
              />
            </div>
          </details>
        </fieldset>
        {start.error && (
          <InlineNotice
            className="mt-5"
            tone="danger"
            title="開始できませんでした"
            action={
              <Button variant="quiet" size="sm" onClick={onSpeechSettings}>
                設定を確認
              </Button>
            }
          >
            {start.error}
          </InlineNotice>
        )}
      </main>
      <StickyActionBar className="px-6 py-4 sm:px-10">
        <div className="mx-auto flex w-full max-w-3xl items-center justify-between gap-4">
          <p className="text-sm text-ink-muted" aria-live="polite">
            {stopFailed
              ? "アプリの再起動が必要です"
              : !state.connected
                ? "接続を確認しています"
                : busy
                  ? start.phase === "preparing"
                    ? "初回の読み込みには時間がかかる場合があります"
                    : start.phase === "cancelling"
                      ? "準備の停止を確認しています…"
                      : "会議を開始しています…"
                  : !audioReady
                    ? "音声認識の準備を完了してください"
                    : "開始すると録音と文字起こしを行います"}
          </p>
          <div className="flex shrink-0 items-center gap-2">
            {start.phase === "preparing" && (
              <Button variant="quiet" onClick={start.cancel}>
                キャンセル
              </Button>
            )}
            <Button
              variant="primary"
              size="lg"
              onClick={startMeeting}
              disabled={!canStart}
              loading={busy}
            >
              {busy
                ? start.phase === "preparing"
                  ? "準備中…"
                  : start.phase === "cancelling"
                    ? "取り消し中…"
                    : "開始中…"
                : "会議を開始"}
            </Button>
          </div>
        </div>
      </StickyActionBar>
    </div>
  );
}

interface FieldProps {
  label: string;
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
}

function Field({ label, value, placeholder, onChange }: FieldProps) {
  const inputId = useId();
  return (
    <label htmlFor={inputId} className="block">
      <span className="mb-1 block text-xs font-semibold text-ink-muted">
        {label}
      </span>
      <input
        id={inputId}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="field text-sm"
      />
    </label>
  );
}

function Area({ label, value, placeholder, onChange }: FieldProps) {
  const inputId = useId();
  return (
    <label htmlFor={inputId} className="block">
      <span className="mb-1 block text-xs font-semibold text-ink-muted">
        {label}
      </span>
      <textarea
        id={inputId}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        rows={2}
        className="field min-h-20 resize-none text-sm leading-5"
      />
    </label>
  );
}
