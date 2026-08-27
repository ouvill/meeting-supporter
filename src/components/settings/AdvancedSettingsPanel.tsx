import type { AiRouteReadModel } from "../../hooks/useAiRoutes";
import { Button } from "../ui/Button";
import { InlineNotice } from "../ui/InlineNotice";
import { FieldRow, SettingsCard, SettingsPage } from "./SettingsPrimitives";
import type { SettingsForm } from "./types";


interface Props {
  form: SettingsForm;
  error?: string;
  acpRoute?: AiRouteReadModel;
  ollamaTesting: boolean;
  ollamaMessage: string;
  ollamaMessageIsError: boolean;
  audioSettingsLocked?: boolean;
  update: <K extends keyof SettingsForm>(
    key: K,
    value: SettingsForm[K],
  ) => void;
  onTestOllama: () => void;
}

export function AdvancedSettingsPanel({
  form,
  error,
  acpRoute,
  ollamaTesting,
  ollamaMessage,
  ollamaMessageIsError,
  audioSettingsLocked = false,
  update,
  onTestOllama,
}: Props) {
  return (
    <SettingsPage
      title="詳細設定"
      description="model識別子、endpoint、command、runtime診断を安全に管理する方向けの設定です。"
    >
      {error && <InlineNotice tone="danger">{error}</InlineNotice>}
      {audioSettingsLocked && (
        <InlineNotice tone="warning">
          会議中は音声認識のmodel設定を変更できません。
        </InlineNotice>
      )}
      {(form.sttBackend === "deepgram" || form.sttBackend === "openai") && (
        <SettingsCard
          title="クラウド音声認識モデル"
          description="選択中の音声認識サービスへ送るmodel識別子です。"
        >
          {form.sttBackend === "deepgram" ? (
            <FieldRow label="Deepgram model識別子">
              <input
                type="text"
                value={form.sttDeepgramModel}
                disabled={audioSettingsLocked}
                onChange={(event) =>
                  update("sttDeepgramModel", event.target.value)
                }
                className="field"
                aria-label="Deepgramモデル"
              />
            </FieldRow>
          ) : (
            <FieldRow label="OpenAI model識別子">
              <select
                value={form.sttOpenaiModel}
                disabled={audioSettingsLocked}
                onChange={(event) =>
                  update("sttOpenaiModel", event.target.value)
                }
                className="field"
                aria-label="OpenAIモデル"
              >
                <option value="gpt-4o-transcribe">gpt-4o-transcribe</option>
                <option value="gpt-4o-mini-transcribe">
                  gpt-4o-mini-transcribe
                </option>
                <option value="whisper-1">whisper-1</option>
              </select>
            </FieldRow>
          )}
        </SettingsCard>
      )}
      <SettingsCard
        title="Ollama 接続設定"
        description="OpenAI互換の /v1 endpointへ接続します。"
      >
        <div className="space-y-4">
          <FieldRow label="ベースURL" hint="通常は変更不要です">
            <input
              type="url"
              value={form.ollamaBaseUrl}
              onChange={(event) => update("ollamaBaseUrl", event.target.value)}
              placeholder="http://localhost:11434/v1"
              className="field"
              aria-label="OllamaベースURL"
            />
          </FieldRow>
          <FieldRow label="接続確認">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                onClick={onTestOllama}
                loading={ollamaTesting}
              >
                接続テスト
              </Button>
              {ollamaMessage && (
                <span
                  className={`text-xs font-medium ${ollamaMessageIsError ? "text-danger" : "text-positive"}`}
                  role={ollamaMessageIsError ? "alert" : "status"}
                >
                  {ollamaMessage}
                </span>
              )}
            </div>
          </FieldRow>
          <InlineNotice tone="warning">
            localhost / 127.0.0.1 / ::1
            以外のURLを指定すると、会議テキストが外部へ送信される可能性があります。
          </InlineNotice>
        </div>
      </SettingsCard>
      {form.sttBackend === "vosk" && (
        <SettingsCard
          title="Vosk 音声認識"
          description="端末に展開したVoskモデルのパスを指定します。"
        >
          <FieldRow label="モデルパス">
            <input
              type="text"
              value={form.sttVoskModelPath}
              disabled={audioSettingsLocked}
              onChange={(event) =>
                update("sttVoskModelPath", event.target.value)
              }
              placeholder="vosk-model-small-ja-0.22"
              className="field"
              aria-label="Voskモデルパス"
            />
          </FieldRow>
        </SettingsCard>
      )}
      <SettingsCard
        title="ACP（実験的機能）"
        description="外部ACP agentをstdioで起動するruntime設定です。"
      >
        <div className="space-y-4">
          <FieldRow label="Runtime">
            <output className="text-sm font-semibold text-ink">
              ACP / stdio
            </output>
          </FieldRow>
          <FieldRow label="Capability">
            <span className="text-sm font-semibold text-ink">返答案生成</span>
          </FieldRow>
          <FieldRow
            label="起動command"
            hint="argvを1行につき1引数で入力します。shell展開は行いません"
          >
            <textarea
              value={form.acpCommand}
              onChange={(event) => update("acpCommand", event.target.value)}
              rows={4}
              placeholder={"python\n/path/to/acp_agent.py"}
              className="field min-h-28 resize-y font-mono text-xs"
              spellCheck={false}
            />
          </FieldRow>
          <InlineNotice
            tone={
              acpRoute?.readiness === "ready"
                ? "positive"
                : acpRoute?.readiness === "error"
                  ? "danger"
                  : "warning"
            }
            title={
              acpRoute?.readiness === "ready"
                ? "起動command設定済み（前回保存時）"
                : "保存済み設定の確認が必要"
            }
          >
            {acpRoute?.message ??
              "保存すると、起動commandが設定されているか確認できます。接続成功を示す状態ではありません。"}
          </InlineNotice>
          <InlineNotice tone="warning" title="実験的な外部連携">
            ACP連携は実験的です。対応する外部エージェントが起動し、接続できる場合だけ利用できます。利用できない状態をアプリ側で代替することはありません。
          </InlineNotice>
        </div>
      </SettingsCard>
    </SettingsPage>
  );
}
