import { useEffect, useId, useRef, useState } from "react";
import {
  ChevronDown,
  FileText,
  Headphones,
  History,
  Mic2,
  Settings,
  Sparkles,
  Trash2,
  Upload,
  Volume2,
} from "lucide-react";
import type {
  Device,
  DeviceId,
  MeetingContextInput,
  ReferenceDocumentInput,
  SendFn,
  SocketState,
} from "../types";
import type {
  AiRoutesReloadStatus,
  AiUseCaseRouteStatus,
} from "../hooks/useAiRoutes";
import { levelToPercent } from "../utils/audioLevel";
import { Button, StickyActionBar, Tooltip } from "./ui";

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
const ACCEPTED_REFERENCE_EXTENSIONS = [".md", ".markdown", ".txt", ".docx"];

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

function createDocumentId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto)
    return crypto.randomUUID();
  return `doc-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function extensionOf(name: string): string {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

function bufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

async function fileToReference(file: File): Promise<ReferenceDocumentInput> {
  const extension = extensionOf(file.name);
  if (!ACCEPTED_REFERENCE_EXTENSIONS.includes(extension)) {
    return {
      id: createDocumentId(),
      name: file.name,
      mimeType: file.type || "application/octet-stream",
      sizeBytes: file.size,
      status: "failed",
      error: "この形式のファイルは追加できません",
    };
  }

  if (extension === ".docx") {
    return {
      id: createDocumentId(),
      name: file.name,
      mimeType:
        file.type ||
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      sizeBytes: file.size,
      contentBase64: bufferToBase64(await file.arrayBuffer()),
      status: "queued",
      error: null,
    };
  }

  return {
    id: createDocumentId(),
    name: file.name,
    mimeType: file.type || "text/plain",
    sizeBytes: file.size,
    text: await file.text(),
    status: "parsed",
    error: null,
  };
}

function contextWithFallback(
  context: MeetingContextInput,
): MeetingContextInput {
  return {
    ...context,
    scenario: context.scenario.trim() || "会議",
    userRole: context.userRole.trim() || "参加者",
    objective: context.objective.trim() || "目的未設定",
    tone: context.tone?.trim() || "簡潔で自然",
  };
}

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
  const [dragActive, setDragActive] = useState(false);
  const [referenceMessage, setReferenceMessage] = useState("");
  const monitors = state.devices.filter((device) => device.is_monitor);
  const mics = state.devices.filter((device) => !device.is_monitor);
  const needsAudioPreparation = ["local", "whisper", "vosk"].includes(
    state.sttBackend,
  );
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

  async function addReferenceFiles(files: FileList | File[]) {
    const incoming = Array.from(files);
    if (!incoming.length) return;
    const documents = await Promise.all(incoming.map(fileToReference));
    setReferences((current) => [...current, ...documents]);
    const failed = documents.filter(
      (document) => document.status === "failed",
    ).length;
    setReferenceMessage(
      failed
        ? `${failed}件は追加できませんでした`
        : `${documents.length}件を追加しました`,
    );
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

            <div>
              <div className="mb-2 flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-ink">参考資料</p>
                  <p className="mt-0.5 text-xs text-ink-muted">
                    文書またはテキストを追加できます
                  </p>
                </div>
                {referenceMessage && (
                  <span className="text-xs text-ink-muted" aria-live="polite">
                    {referenceMessage}
                  </span>
                )}
              </div>
              <label
                onDragEnter={(event) => {
                  event.preventDefault();
                  setDragActive(true);
                }}
                onDragOver={(event) => event.preventDefault()}
                onDragLeave={(event) => {
                  event.preventDefault();
                  setDragActive(false);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragActive(false);
                  void addReferenceFiles(event.dataTransfer.files);
                }}
                className={`flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-4 text-center text-xs font-medium transition-colors motion-reduce:transition-none ${
                  dragActive
                    ? "border-primary bg-primary-soft text-primary"
                    : "border-line-strong bg-paper text-ink-muted hover:border-primary/45 hover:text-primary"
                }`}
              >
                <Upload aria-hidden="true" size={16} />
                ドロップするか、ファイルを選ぶ
                <input
                  type="file"
                  multiple
                  accept=".md,.markdown,.txt,.docx"
                  className="sr-only"
                  onChange={(event) => {
                    if (event.currentTarget.files)
                      void addReferenceFiles(event.currentTarget.files);
                    event.currentTarget.value = "";
                  }}
                />
              </label>
              {references.length > 0 && (
                <ul className="mt-2 space-y-1.5" aria-label="追加した資料">
                  {references.map((document) => (
                    <li
                      key={document.id}
                      className="flex items-center justify-between gap-3 rounded-xl border border-line bg-paper px-3 py-2 text-xs"
                    >
                      <div className="min-w-0">
                        <p className="truncate font-medium text-ink">
                          {document.name}
                        </p>
                        <p
                          className={
                            document.status === "failed"
                              ? "text-danger"
                              : "text-ink-muted"
                          }
                        >
                          {document.status === "failed"
                            ? document.error
                            : "追加済み"}
                        </p>
                      </div>
                      <Tooltip content={`${document.name}を削除`}>
                        <button
                          type="button"
                          onClick={() =>
                            setReferences((current) =>
                              current.filter((item) => item.id !== document.id),
                            )
                          }
                          aria-label={`${document.name}を削除`}
                          className="rounded-lg p-1.5 text-ink-muted transition-colors hover:bg-surface hover:text-danger motion-reduce:transition-none"
                        >
                          <Trash2 aria-hidden="true" size={15} />
                        </button>
                      </Tooltip>
                    </li>
                  ))}
                </ul>
              )}
            </div>
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

type IconComponent = typeof Mic2;

interface DeviceSelectProps {
  label: string;
  icon: IconComponent;
  value: DeviceId;
  monitors: Device[];
  mics: Device[];
  primary: "monitors" | "mics";
  disabled: boolean;
  level: number;
  onChange: (value: DeviceId) => void;
}

function DeviceSelect({
  label,
  icon: Icon,
  value,
  monitors,
  mics,
  primary,
  disabled,
  level,
  onChange,
}: DeviceSelectProps) {
  const selectId = useId();
  const first = primary === "monitors" ? monitors : mics;
  const second = primary === "monitors" ? mics : monitors;
  const firstLabel = primary === "monitors" ? "スピーカー" : "マイク";
  const secondLabel = primary === "monitors" ? "マイク" : "スピーカー";
  const defaultDevice =
    first.find((device) => device.is_default) ??
    second.find((device) => device.is_default);
  const defaultLabel = primary === "monitors" ? "既定スピーカー" : "既定マイク";
  const defaultOptionLabel = defaultDevice
    ? `${defaultLabel}（${defaultDevice.name}）`
    : defaultLabel;
  const color = primary === "monitors" ? "bg-cue" : "bg-positive";

  function handleChange(rawValue: string) {
    if (!rawValue) {
      onChange(null);
      return;
    }
    const numericValue = Number(rawValue);
    onChange(Number.isNaN(numericValue) ? rawValue : numericValue);
  }

  return (
    <div className="rounded-xl bg-paper p-3">
      <div className="mb-2 flex items-center gap-2">
        <Icon
          aria-hidden="true"
          size={15}
          className={primary === "monitors" ? "text-cue" : "text-positive"}
        />
        <label htmlFor={selectId} className="text-sm font-semibold text-ink">
          {label}
        </label>
        <AudioLevelMeter level={level} color={color} />
      </div>
      <select
        id={selectId}
        value={value === null || value === undefined ? "" : String(value)}
        onChange={(event) => handleChange(event.target.value)}
        disabled={disabled}
        className="field text-sm"
      >
        <option value="">{defaultOptionLabel}</option>
        {first.length > 0 && (
          <optgroup label={firstLabel}>
            {first.map((device) => (
              <option key={String(device.index)} value={String(device.index)}>
                {device.name}
              </option>
            ))}
          </optgroup>
        )}
        {second.length > 0 && (
          <optgroup label={secondLabel}>
            {second.map((device) => (
              <option key={String(device.index)} value={String(device.index)}>
                {device.name}
              </option>
            ))}
          </optgroup>
        )}
      </select>
    </div>
  );
}

type SystemAudioTestStatus = "idle" | "playing" | "played" | "error";

interface SystemAudioPlayback {
  context: AudioContext;
  oscillator: OscillatorNode;
}

function SystemAudioTestControl() {
  const playbackRef = useRef<SystemAudioPlayback | null>(null);
  const [status, setStatus] = useState<SystemAudioTestStatus>("idle");

  useEffect(
    () => () => {
      const playback = playbackRef.current;
      playbackRef.current = null;
      if (!playback) return;
      playback.oscillator.onended = null;
      try {
        playback.oscillator.stop();
      } catch {
        // The oscillator may already have ended.
      }
      void playback.context.close().catch(() => undefined);
    },
    [],
  );

  async function playTestSound() {
    if (playbackRef.current) return;
    setStatus("playing");

    let context: AudioContext | null = null;
    try {
      if (typeof window.AudioContext !== "function") {
        setStatus("error");
        return;
      }

      const audioContext = new window.AudioContext();
      context = audioContext;
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      playbackRef.current = { context: audioContext, oscillator };

      oscillator.type = "sine";
      oscillator.connect(gain);
      gain.connect(audioContext.destination);
      await audioContext.resume();

      const startAt = audioContext.currentTime + 0.02;
      const stopAt = startAt + 0.7;
      oscillator.frequency.setValueAtTime(523.25, startAt);
      oscillator.frequency.linearRampToValueAtTime(659.25, startAt + 0.35);
      gain.gain.setValueAtTime(0, startAt);
      gain.gain.linearRampToValueAtTime(0.14, startAt + 0.04);
      gain.gain.setValueAtTime(0.14, stopAt - 0.1);
      gain.gain.linearRampToValueAtTime(0, stopAt);

      oscillator.onended = () => {
        if (playbackRef.current?.context !== audioContext) return;
        playbackRef.current = null;
        void audioContext.close().catch(() => undefined);
        setStatus("played");
      };
      oscillator.start(startAt);
      oscillator.stop(stopAt);
    } catch {
      playbackRef.current = null;
      if (context) void context.close().catch(() => undefined);
      setStatus("error");
    }
  }

  return (
    <div className="-mt-1 rounded-xl border border-line bg-surface px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-ink">相手側の音声をテスト</p>
          <p className="mt-0.5 text-xs leading-5 text-ink-muted">
            この端末の既定の出力から短い音を流します。
          </p>
        </div>
        <Button
          variant="quiet"
          size="sm"
          onClick={() => void playTestSound()}
          disabled={status === "playing"}
          className="shrink-0"
        >
          <Volume2 aria-hidden="true" className="size-3.5" />
          {status === "playing" ? "再生中…" : "テスト音を再生"}
        </Button>
      </div>
      {status === "played" && (
        <p
          role="status"
          aria-live="polite"
          className="mt-1 text-xs leading-5 text-positive"
        >
          相手側の音量バーが動いたか確認してください。
        </p>
      )}
      {status === "error" && (
        <p
          role="status"
          aria-live="polite"
          className="mt-1 text-xs leading-5 text-danger"
        >
          テスト音を再生できませんでした。端末の音量設定を確認してください。
        </p>
      )}
    </div>
  );
}

function AudioLevelMeter({ level, color }: { level: number; color: string }) {
  return (
    <div
      className="ml-auto h-1.5 w-24 overflow-hidden rounded-full bg-line"
      aria-label={`入力レベル ${Math.round(levelToPercent(level))}%`}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(levelToPercent(level))}
    >
      <div
        className={`h-full rounded-full ${color} transition-[width] duration-75 motion-reduce:transition-none`}
        style={{ width: `${levelToPercent(level)}%` }}
      />
    </div>
  );
}

interface AudioPreparationProps {
  initialized: boolean;
  initializing: boolean;
  initRequested: boolean;
  failed: boolean;
  onInit: () => void;
  onShutdown: () => void;
}

function AudioPreparation({
  initialized,
  initializing,
  initRequested,
  failed,
  onInit,
  onShutdown,
}: AudioPreparationProps) {
  const preparing = initializing || initRequested;

  if (initialized) {
    return (
      <div
        className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-positive/20 bg-positive-soft px-3 py-2.5"
        aria-live="polite"
      >
        <p className="text-xs font-semibold text-positive">
          音声認識を使えます
        </p>
        <button
          type="button"
          onClick={onShutdown}
          className="rounded-lg px-2 py-1 text-xs font-medium text-ink-muted hover:bg-surface hover:text-danger"
        >
          やり直す
        </button>
      </div>
    );
  }

  if (preparing) {
    return (
      <div
        className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-warning/20 bg-warning-soft px-3 py-2.5"
        aria-live="polite"
      >
        <span className="flex items-center gap-2 text-xs font-semibold text-warning">
          <span
            className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-warning border-t-transparent motion-reduce:animate-none"
            aria-hidden="true"
          />
          音声認識を準備しています…
        </span>
        <button
          type="button"
          onClick={onShutdown}
          className="rounded-lg px-2 py-1 text-xs font-medium text-ink-muted hover:bg-surface hover:text-danger"
        >
          キャンセル
        </button>
      </div>
    );
  }

  return (
    <div className="mt-3 space-y-2" aria-live="polite">
      {failed && (
        <p className="rounded-xl border border-danger/20 bg-danger-soft px-3 py-2 text-xs font-medium text-danger">
          音声認識を準備できませんでした。もう一度お試しください。
        </p>
      )}
      <p className="text-xs leading-5 text-ink-muted">
        初回は必要なデータの読み込みに時間がかかる場合があります。
      </p>
      <button
        type="button"
        onClick={onInit}
        className="w-full rounded-xl border border-line-strong bg-surface px-3 py-2.5 text-xs font-bold text-ink transition-colors hover:border-primary/45 hover:bg-primary-soft hover:text-primary motion-reduce:transition-none"
      >
        音声認識を使えるようにする
      </button>
    </div>
  );
}
