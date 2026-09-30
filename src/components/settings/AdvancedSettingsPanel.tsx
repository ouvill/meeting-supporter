import { Button } from "../ui/Button";
import { InlineNotice } from "../ui/InlineNotice";
import { FieldRow, SettingsCard, SettingsPage } from "./SettingsPrimitives";
import type { SettingsForm } from "./types";

interface Props {
  form: SettingsForm;
  error?: string;
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
      description="音声認識モデルとOllamaの接続先を設定します。"
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
    </SettingsPage>
  );
}
