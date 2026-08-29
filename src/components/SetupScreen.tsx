import { useId, useState } from "react";
import {
  ChevronDown,
  FileText,
  Headphones,
  History,
  Mic2,
  Settings,
  Sparkles,
  Volume2,
} from "lucide-react";
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
import {
  AudioPreparation,
  SystemAudioTestControl,
} from "./setup/AudioPreparation";
import { DeviceSelect } from "./setup/DeviceSelect";
import { ReferenceDocuments } from "./setup/ReferenceDocuments";
import { contextWithFallback } from "./setup/setupUtils";
import { Button, StickyActionBar } from "./ui";

interface Props {
  state: SocketState;
  send: SendFn;
  showFirstRunGuidance: boolean;
  onSettings: () => void;
  onHistory?: () => void;
  replyStatus: AiUseCaseRouteStatus;
  replyReloadStatus: AiRoutesReloadStatus;
  onReloadReplyStatus: () => void;
}

const MEETING_TYPES = ["商談", "面接", "1on1", "定例", "相談", "その他"];
const ROLE_PRESETS = ["進行役", "提案する側", "聞き手", "意思決定者", "参加者"];

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

export function SetupScreen({
  state,
  send,
  showFirstRunGuidance,
  onSettings,
  onHistory,
  replyStatus,
  replyReloadStatus,
  onReloadReplyStatus,
}: Props) {
  const [meetingContext, setMeetingContext] = useState<MeetingContextInput>(
    DEFAULT_MEETING_CONTEXT,
  );
  const [references, setReferences] = useState<ReferenceDocumentInput[]>([]);
  const monitors = state.devices.filter((device) => device.is_monitor);
  const mics = state.devices.filter((device) => !device.is_monitor);
  const needsAudioPreparation = [
    "local",
    "whisper",
    "reazonspeech",
    "vosk",
  ].includes(state.sttBackend);
  const audioLocked =
    state.sttInitialized || state.sttInitializing || state.sttInitRequested;
  const audioReady = !needsAudioPreparation || state.sttInitialized;
  const canStart = state.connected && audioReady;
  const replyFeatureEnabled = state.agentSettings.replyEnabled;
  const replyFeatureReady = replyFeatureEnabled && replyStatus.canGenerate;
  const replyReadinessLoading =
    replyFeatureEnabled &&
    replyStatus.readiness === "unknown" &&
    replyStatus.message === null;
  const showReplyRecovery =
    canStart && !replyFeatureReady && !replyReadinessLoading;
  const retryReplyReadiness =
    replyFeatureEnabled &&
    (replyStatus.readiness === "error" ||
      replyStatus.readiness === "unavailable");
  const startStatus = !state.connected
    ? "接続を確認しています"
    : !audioReady
      ? "音声認識の準備が必要です"
      : replyFeatureReady
        ? "開始できます"
        : replyReadinessLoading
          ? "AIの準備を確認しています…"
          : "会話の記録は開始できます。返答案は現在利用できません。";
  const startStatusDetail =
    canStart && !replyFeatureReady
      ? !replyFeatureEnabled
        ? "返答案は設定でオフになっています。"
        : (replyStatus.message ?? "返答案の確認中も会議を開始できます。")
      : "会議中も支援の設定は変更できます";

  function updateContext<K extends keyof MeetingContextInput>(
    key: K,
    value: MeetingContextInput[K],
  ) {
    setMeetingContext((current) => ({ ...current, [key]: value }));
  }

  function startMeeting() {
    if (!canStart) return;
    send({
      type: "start_meeting",
      meeting_context: contextWithFallback(meetingContext),
      references: references.filter((document) => document.status !== "failed"),
    });
  }

  return (
    <div
      data-testid="setup-screen"
      className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-paper text-ink"
    >
      <main className="mx-auto w-full max-w-2xl flex-1 px-5 pb-8 pt-6 sm:px-7">
        <div className="mb-6 flex items-start gap-3">
          <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-primary text-white shadow-sm">
            <Sparkles aria-hidden="true" size={18} />
          </div>
          <div>
            <h1 className="font-display text-2xl font-bold tracking-[0.01em] text-ink">
              {showFirstRunGuidance
                ? "まず、音声を確認しましょう"
                : "次の会議を準備しましょう"}
            </h1>
            <p className="mt-1 text-sm leading-6 text-ink-muted">
              {showFirstRunGuidance
                ? "音が届くことを確かめれば、AIの設定はあとからでも大丈夫です。"
                : "会議について、わかる範囲で教えてください。"}
            </p>
          </div>
        </div>

        <section
          className="rounded-2xl border border-line bg-surface p-4 shadow-sm"
          aria-labelledby="audio-check-heading"
        >
          <div className="mb-3 flex items-start gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-positive-soft text-positive">
              <Headphones aria-hidden="true" size={17} />
            </div>
            <div>
              <h2
                id="audio-check-heading"
                className="font-display text-base font-bold text-ink"
              >
                音声チェック
              </h2>
              <p className="mt-0.5 text-xs leading-5 text-ink-muted">
                話すか、相手の声を流してバーが動くか確認します。
              </p>
            </div>
          </div>

          <div className="space-y-3">
            <DeviceSelect
              label="相手側の音声"
              icon={Volume2}
              value={state.deviceOther}
              monitors={monitors}
              mics={mics}
              primary="monitors"
              disabled={audioLocked}
              level={state.levelOther}
              onChange={(value) =>
                send({ type: "set_device", role: "other", device: value })
              }
            />
            <SystemAudioTestControl />
            <DeviceSelect
              label="自分のマイク"
              icon={Mic2}
              value={state.deviceSelf}
              monitors={monitors}
              mics={mics}
              primary="mics"
              disabled={audioLocked}
              level={state.levelSelf}
              onChange={(value) =>
                send({ type: "set_device", role: "self", device: value })
              }
            />
          </div>

          {needsAudioPreparation && (
            <AudioPreparation
              initialized={state.sttInitialized}
              initializing={state.sttInitializing}
              initRequested={state.sttInitRequested}
              failed={state.statusText.trim().startsWith("エラー:")}
              onInit={() => send({ type: "init_stt" })}
              onShutdown={() => send({ type: "shutdown_stt" })}
            />
          )}
        </section>

        <section
          className="mt-4 rounded-2xl border border-line bg-surface p-4 shadow-sm"
          aria-labelledby="reply-readiness-heading"
        >
          <div className="flex items-start gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-primary-soft text-primary">
              <Sparkles aria-hidden="true" size={17} />
            </div>
            <div className="min-w-0 flex-1">
              <h2
                id="reply-readiness-heading"
                className="font-display text-base font-bold text-ink"
              >
                返答案
              </h2>
              <p
                role="status"
                aria-live="polite"
                className="mt-1 text-sm leading-6 text-ink"
              >
                {!state.agentSettings.replyEnabled
                  ? "返答案は設定でオフになっています。"
                  : replyStatus.canGenerate
                    ? "返答案を利用できます。"
                    : (replyStatus.message ?? "返答案の準備を確認しています。")}
              </p>
              <p className="mt-1 text-xs leading-5 text-ink-muted">
                {showFirstRunGuidance
                  ? "返答案は後から設定できます。今は録音と文字起こしだけでも会議を開始できます。"
                  : "返答案を利用できない場合も、録音と文字起こしは開始できます。"}
              </p>
            </div>
            {!state.agentSettings.replyEnabled ||
            replyStatus.readiness === "setup_required" ? (
              <Button variant="quiet" size="sm" onClick={onSettings}>
                {showFirstRunGuidance ? "AIも準備する" : "設定"}
              </Button>
            ) : !replyStatus.canGenerate &&
              replyStatus.readiness !== "unknown" ? (
              <Button
                variant="quiet"
                size="sm"
                onClick={onReloadReplyStatus}
                disabled={replyReloadStatus === "loading"}
              >
                {replyReloadStatus === "loading" ? "確認中…" : "再確認"}
              </Button>
            ) : null}
          </div>
        </section>

        <div className="mb-3 mt-6">
          <h2 className="font-display text-lg font-bold text-ink">
            会議について
          </h2>
          <p className="mt-1 text-sm leading-6 text-ink-muted">
            すべて任意です。空欄のままでも会議を開始できます。
          </p>
        </div>

        <div className="space-y-4">
          <DecisionCard number="1" title="どんな会議ですか？">
            <div
              className="flex flex-wrap gap-2"
              role="group"
              aria-label="会議種別"
            >
              {MEETING_TYPES.map((meetingType) => {
                const selected = meetingContext.scenario === meetingType;
                return (
                  <button
                    key={meetingType}
                    type="button"
                    aria-pressed={selected}
                    onClick={() =>
                      updateContext("scenario", selected ? "" : meetingType)
                    }
                    className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors motion-reduce:transition-none ${
                      selected
                        ? "border-primary bg-primary text-white shadow-sm"
                        : "border-line bg-surface text-ink hover:border-primary/45 hover:bg-primary-soft"
                    }`}
                  >
                    {meetingType}
                  </button>
                );
              })}
            </div>
            <Field
              label="上にない場合"
              value={meetingContext.scenario}
              placeholder="例：プロジェクトの振り返り"
              onChange={(value) => updateContext("scenario", value)}
            />
          </DecisionCard>

          <DecisionCard number="2" title="今日、何を持ち帰りたいですか？">
            <label className="block">
              <span className="sr-only">今日持ち帰りたいこと</span>
              <textarea
                value={meetingContext.objective}
                onChange={(event) =>
                  updateContext("objective", event.target.value)
                }
                placeholder="例：次回までの担当と期限を決めたい"
                rows={2}
                className="field resize-none text-sm leading-6"
              />
            </label>
          </DecisionCard>

          <DecisionCard number="3" title="あなたの立場は？">
            <div
              className="flex flex-wrap gap-2"
              role="group"
              aria-label="あなたの立場"
            >
              {ROLE_PRESETS.map((role) => {
                const selected = meetingContext.userRole === role;
                return (
                  <button
                    key={role}
                    type="button"
                    aria-pressed={selected}
                    onClick={() =>
                      updateContext("userRole", selected ? "" : role)
                    }
                    className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors motion-reduce:transition-none ${
                      selected
                        ? "border-primary bg-primary text-white"
                        : "border-line bg-surface text-ink-muted hover:border-primary/45"
                    }`}
                  >
                    {role}
                  </button>
                );
              })}
            </div>
            <Field
              label="上にない場合"
              value={meetingContext.userRole}
              placeholder="例：採用担当、顧客側の責任者"
              onChange={(value) => updateContext("userRole", value)}
            />
          </DecisionCard>
        </div>

        <details className="group mt-4 rounded-2xl border border-line bg-surface shadow-sm">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3.5 text-sm font-semibold text-ink outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2">
            <span className="flex items-center gap-2">
              <FileText
                aria-hidden="true"
                size={16}
                className="text-ink-muted"
              />
              任意の詳細・資料
            </span>
            <ChevronDown
              aria-hidden="true"
              size={17}
              className="transition-transform group-open:rotate-180 motion-reduce:transition-none"
            />
          </summary>
          <div className="space-y-4 border-t border-line px-4 py-4">
            <div className="grid gap-3 sm:grid-cols-2">
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
            <div className="grid gap-3 sm:grid-cols-2">
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
            </div>

            <ReferenceDocuments
              references={references}
              onChange={setReferences}
            />
          </div>
        </details>

        <nav
          className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2"
          aria-label="補助メニュー"
        >
          {onHistory && (
            <button
              type="button"
              onClick={onHistory}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-muted hover:text-primary"
            >
              <History aria-hidden="true" size={14} />
              過去の会議
            </button>
          )}
          <button
            type="button"
            onClick={onSettings}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-muted hover:text-ink"
          >
            <Settings aria-hidden="true" size={14} />
            設定
          </button>
        </nav>
      </main>

      <StickyActionBar className="px-5 py-3 sm:px-7">
        <div className="mx-auto flex w-full max-w-2xl items-center gap-3">
          <div className="min-w-0 flex-1" aria-live="polite">
            <p className="text-sm font-semibold leading-5 text-ink">
              {startStatus}
            </p>
            <p className="text-xs leading-5 text-ink-muted">
              {startStatusDetail}
            </p>
          </div>
          {showReplyRecovery && (
            <Button
              variant="quiet"
              size="sm"
              onClick={retryReplyReadiness ? onReloadReplyStatus : onSettings}
              disabled={replyReloadStatus === "loading"}
              className="shrink-0"
            >
              {retryReplyReadiness ? "もう一度試す" : "AIの準備を確認"}
            </Button>
          )}
          <Button
            variant="primary"
            size="lg"
            onClick={startMeeting}
            disabled={!canStart}
            className="shrink-0 rounded-xl px-5 text-sm font-bold motion-reduce:transform-none motion-reduce:transition-none"
          >
            会議を開始
          </Button>
        </div>
      </StickyActionBar>
    </div>
  );
}

interface DecisionCardProps {
  number: string;
  title: string;
  children: React.ReactNode;
}

function DecisionCard({ number, title, children }: DecisionCardProps) {
  return (
    <section className="rounded-2xl border border-line bg-surface p-4 shadow-sm">
      <div className="mb-3 flex items-center gap-2.5">
        <span
          className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-white"
          aria-hidden="true"
        >
          {number}
        </span>
        <h3 className="font-display text-base font-bold text-ink">{title}</h3>
      </div>
      <div className="space-y-3">{children}</div>
    </section>
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
