import type { SpeechModelController } from "../../hooks/useSpeechModel";
import type { ManagedSttAvailability } from "../../hooks/useManagedService";
import { InlineNotice } from "../ui/InlineNotice";
import { Button } from "../ui/Button";
import {
  ApiConnectionControl,
  CONNECTIONS,
  type ConnectionProvider,
} from "./ApiConnectionControl";
import { SpeechModelPreparationCard } from "./SpeechModelPreparationCard";
import { FieldRow, SettingsCard, SettingsPage } from "./SettingsPrimitives";
import {
  isVadEngine,
  type ConnectionUiState,
  type SettingsFieldErrors,
  type SettingsForm,
} from "./types";

const CLOUD_STT = {
  deepgram: { label: "Deepgram" },
  openai: { label: "OpenAI" },
  xai: { label: "Grok / xAI" },
} as const satisfies Partial<Record<ConnectionProvider, { label: string }>>;

interface Props {
  form: SettingsForm;
  errors: SettingsFieldErrors;
  speechModel: SpeechModelController;
  speechModelActionsDisabled?: boolean;
  audioSettingsLocked?: boolean;
  managedStt: ManagedSttAvailability;
  onManageAccount: () => void;
  connectionStates: Record<ConnectionProvider, ConnectionUiState>;
  secretsStatus: Record<string, boolean>;
  secretInputs: Record<string, string>;
  connectionEditingProvider: ConnectionProvider | null;
  connectionTestingProvider: ConnectionProvider | null;
  connectionTestMessages: Partial<Record<ConnectionProvider, string>>;
  onBeginConnectionEdit: (provider: ConnectionProvider) => void;
  onCancelConnectionEdit: (provider: ConnectionProvider) => void;
  onSecretChange: (provider: ConnectionProvider, value: string) => void;
  onTestConnection: (provider: ConnectionProvider) => void;
  onRequestSecretDelete: (provider: ConnectionProvider) => void;
  onCancelSecretDelete: (provider: ConnectionProvider) => void;
  update: <K extends keyof SettingsForm>(
    key: K,
    value: SettingsForm[K],
  ) => void;
}

export function AudioSettingsPanel({
  form,
  errors,
  speechModel,
  speechModelActionsDisabled = false,
  audioSettingsLocked = false,
  managedStt,
  onManageAccount,
  connectionStates,
  secretsStatus,
  secretInputs,
  connectionEditingProvider,
  connectionTestingProvider,
  connectionTestMessages,
  onBeginConnectionEdit,
  onCancelConnectionEdit,
  onSecretChange,
  onTestConnection,
  onRequestSecretDelete,
  onCancelSecretDelete,
  update,
}: Props) {
  const speechModelControlsDisabled =
    audioSettingsLocked ||
    speechModel.blocksSettingsSave ||
    speechModelActionsDisabled;
  const cloudProvider =
    form.sttBackend in CLOUD_STT
      ? (form.sttBackend as keyof typeof CLOUD_STT)
      : null;
  const cloud = cloudProvider ? CLOUD_STT[cloudProvider] : null;
  const usesLocalSpeechModel =
    form.sttBackend === "vosk" ||
    form.sttBackend === "whisper" ||
    form.sttBackend === "reazonspeech";
  return (
    <SettingsPage
      title="音声"
      description="会議の音声を文字にする方法と、発話の区切り方を設定します。端末内で処理する方法をおすすめします。"
    >
      {audioSettingsLocked && (
        <InlineNotice tone="warning">
          会議中は音声認識の設定を変更できません。会議を終了してから変更してください。
        </InlineNotice>
      )}
      <fieldset
        disabled={audioSettingsLocked}
        className="m-0 min-w-0 space-y-5 border-0 p-0"
      >
        <SettingsCard title="聞き取り方法">
          <div className="space-y-4">
            <FieldRow
              label="処理方法"
              hint="端末内の処理では音声を外部へ送りません"
              error={errors.audio}
            >
              <select
                aria-label="音声認識方式"
                value={form.sttBackend}
                disabled={speechModelControlsDisabled}
                onChange={(event) => update("sttBackend", event.target.value)}
                className="field"
              >
                <option value="whisper">端末内・高精度（おすすめ）</option>
                <option value="reazonspeech">
                  端末内・日本語高精度（ReazonSpeech）
                </option>
                <option value="vosk">端末内・軽量</option>
                <option value="managed" disabled={!managedStt.selectable}>
                  Meeting Supporter AI（共通利用枠）
                </option>
                <option value="deepgram">Deepgram（クラウド処理）</option>
                <option value="openai">OpenAI（クラウド処理）</option>
                <option value="xai">Grok / xAI（クラウド処理）</option>
                {form.sttBackend === "dummy" && (
                  <option value="dummy">テスト用</option>
                )}
              </select>
            </FieldRow>
            {managedStt.offered && (
              <InlineNotice tone={managedStt.selectable ? "info" : "warning"}>
                <p>{managedStt.message}</p>
                {!managedStt.selectable && (
                  <Button
                    size="sm"
                    variant="quiet"
                    className="mt-2"
                    onClick={onManageAccount}
                  >
                    アカウントとプランを確認
                  </Button>
                )}
              </InlineNotice>
            )}
            {cloudProvider && (
              <ApiConnectionControl
                provider={cloudProvider}
                state={connectionStates[cloudProvider]}
                hasSavedKey={
                  secretsStatus[CONNECTIONS[cloudProvider].secretKey] ?? false
                }
                draftKey={
                  secretInputs[CONNECTIONS[cloudProvider].secretKey] ?? ""
                }
                editing={connectionEditingProvider === cloudProvider}
                testing={connectionTestingProvider === cloudProvider}
                disabled={
                  connectionTestingProvider !== null &&
                  connectionTestingProvider !== cloudProvider
                }
                testMessage={connectionTestMessages[cloudProvider] ?? null}
                onBeginEdit={() => onBeginConnectionEdit(cloudProvider)}
                onCancelEdit={() => onCancelConnectionEdit(cloudProvider)}
                onDraftChange={(value) => onSecretChange(cloudProvider, value)}
                onTest={() => onTestConnection(cloudProvider)}
                onRequestDelete={() => onRequestSecretDelete(cloudProvider)}
                onCancelDelete={() => onCancelSecretDelete(cloudProvider)}
              />
            )}

            {cloud && (
              <InlineNotice tone="warning">
                音声データは {cloud.label}{" "}
                に送信され、利用料は各サービスの契約先から請求されます。APIキーはこの画面で設定します。モデル識別子は詳細設定で管理します。
              </InlineNotice>
            )}

            {form.sttBackend === "reazonspeech" && (
              <InlineNotice tone="info">
                ReazonSpeech
                K2-v2の軽量化モデルを端末内で実行します。日本語専用で、モデルの取得に約153
                MB使用します。
              </InlineNotice>
            )}

            {form.sttBackend === "whisper" && (
              <FieldRow
                label="精度と速さ"
                hint="高精度ほど端末への負荷が大きくなります"
              >
                <select
                  aria-label="聞き取りの精度と速さ"
                  value={form.sttWhisperModel}
                  disabled={speechModelControlsDisabled}
                  onChange={(event) =>
                    update("sttWhisperModel", event.target.value)
                  }
                  className="field"
                >
                  <option value="tiny">最速</option>
                  <option value="base">軽量</option>
                  <option value="small">バランス</option>
                  <option value="medium">高精度</option>
                  <option value="large-v2">より高精度</option>
                  <option value="large-v3-turbo">最高精度（おすすめ）</option>
                </select>
              </FieldRow>
            )}
            <FieldRow
              label="会議の言語"
              hint={
                form.sttBackend === "reazonspeech"
                  ? "ReazonSpeech K2-v2は日本語専用です"
                  : speechModelControlsDisabled
                    ? "データの準備中は言語を変更できません"
                    : undefined
              }
            >
              <select
                value={form.sttLang}
                onChange={(event) => update("sttLang", event.target.value)}
                disabled={
                  speechModelControlsDisabled ||
                  form.sttBackend === "reazonspeech"
                }
                className="field"
                aria-label="会議の言語"
              >
                <option value="ja">日本語</option>
                {form.sttBackend !== "reazonspeech" && (
                  <option value="en">英語</option>
                )}
                {!["ja", "en"].includes(form.sttLang) && (
                  <option value={form.sttLang}>{form.sttLang}</option>
                )}
              </select>
            </FieldRow>
          </div>
        </SettingsCard>
        {usesLocalSpeechModel && (
          <SpeechModelPreparationCard
            model={speechModel}
            startDisabled={speechModelActionsDisabled || audioSettingsLocked}
          />
        )}
        <SettingsCard
          title="発話の区切り"
          description="話し終わりの検出を調整します。通常は変更する必要はありません。"
        >
          <div className="space-y-4">
            <FieldRow
              label="声の検出方法"
              hint="SileroはTorchを使わず、同梱した約208 KBのONNXモデルを端末内で実行します"
            >
              <select
                value={form.sttVadEngine}
                disabled={audioSettingsLocked}
                onChange={(event) => {
                  const value = event.target.value;
                  if (isVadEngine(value)) {
                    update("sttVadEngine", value);
                  }
                }}
                className="field"
                aria-label="声の検出方法"
              >
                <option value="silero">Silero VAD（高精度・おすすめ）</option>
                <option value="webrtc">WebRTC VAD（最軽量）</option>
              </select>
            </FieldRow>
            <FieldRow
              label="無音とみなす時間"
              hint="短いほど返答案を早く作り始めます"
            >
              <div className="flex items-center gap-2">
                <input
                  type="range"
                  aria-label="無音判定（秒）"
                  value={form.sttSilence}
                  disabled={audioSettingsLocked}
                  min={0.1}
                  max={5}
                  step={0.1}
                  onChange={(event) =>
                    update("sttSilence", Number(event.target.value))
                  }
                  className="min-w-0 flex-1 accent-primary"
                />
                <output className="w-12 text-right text-sm font-semibold tabular-nums text-ink">
                  {form.sttSilence.toFixed(1)} 秒
                </output>
              </div>
            </FieldRow>
            {form.sttVadEngine === "silero" ? (
              <FieldRow
                label="音声判定しきい値"
                hint="低いほど小さな声を拾い、高いほど雑音を除外します"
              >
                <div className="flex items-center gap-2">
                  <input
                    type="range"
                    aria-label="Silero音声判定しきい値"
                    value={form.sttVadSensitivity}
                    disabled={audioSettingsLocked}
                    min={0.05}
                    max={0.95}
                    step={0.05}
                    onChange={(event) =>
                      update("sttVadSensitivity", Number(event.target.value))
                    }
                    className="min-w-0 flex-1 accent-primary"
                  />
                  <output className="w-12 text-right text-sm font-semibold tabular-nums text-ink">
                    {Math.round(form.sttVadSensitivity * 100)}%
                  </output>
                </div>
              </FieldRow>
            ) : (
              <FieldRow label="声の検出感度">
                <select
                  value={form.sttVad}
                  disabled={audioSettingsLocked}
                  onChange={(event) =>
                    update("sttVad", Number(event.target.value))
                  }
                  className="field"
                  aria-label="声の検出感度"
                >
                  <option value={0}>低い</option>
                  <option value={1}>やや低い</option>
                  <option value={2}>標準</option>
                  <option value={3}>高い</option>
                </select>
              </FieldRow>
            )}
          </div>
        </SettingsCard>
      </fieldset>
    </SettingsPage>
  );
}
