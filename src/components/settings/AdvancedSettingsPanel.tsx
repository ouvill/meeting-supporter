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
  update,
  onTestOllama,
}: Props) {
  return (
    <SettingsPage title="詳細設定" description="Ollamaの接続先を設定します。">
      {error && <InlineNotice tone="danger">{error}</InlineNotice>}
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
