import { useEffect, useState } from "react";
import { getAiModels } from "../../api/generated/sdk.gen";
import type {
  AiModelOption,
  ConnectionProvider,
} from "../../api/generated/types.gen";
import { Button } from "../ui/Button";
import { InlineNotice } from "../ui/InlineNotice";
import { FieldRow } from "./SettingsPrimitives";
import type { SettingsForm } from "./types";

interface Props {
  provider: string;
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

const CUSTOM_MODEL = "__custom_model__";
const CLOUD_MODELS = [
  { provider: "openai", label: "OpenAI", field: "openaiModel" },
  { provider: "gemini", label: "Google Gemini", field: "geminiModel" },
  { provider: "anthropic", label: "Anthropic", field: "anthropicModel" },
] as const satisfies readonly {
  provider: ConnectionProvider;
  label: string;
  field: keyof SettingsForm;
}[];

type ModelCatalog = {
  loading: boolean;
  options: AiModelOption[];
  message: string | null;
};

const EMPTY_CATALOG: ModelCatalog = {
  loading: true,
  options: [],
  message: null,
};

export function AiModelSettings({
  provider: selectedProvider,
  form,
  error,
  ollamaTesting,
  ollamaMessage,
  ollamaMessageIsError,
  update,
  onTestOllama,
}: Props) {
  const [catalogs, setCatalogs] = useState<
    Record<ConnectionProvider, ModelCatalog>
  >({
    openai: EMPTY_CATALOG,
    gemini: EMPTY_CATALOG,
    anthropic: EMPTY_CATALOG,
  });
  const [customModels, setCustomModels] = useState<Set<keyof SettingsForm>>(
    new Set(),
  );

  useEffect(() => {
    const controller = new AbortController();
    for (const { provider } of CLOUD_MODELS.filter(
      (item) => item.provider === selectedProvider,
    )) {
      void getAiModels({ query: { provider }, signal: controller.signal })
        .then(({ data, error }) => {
          if (controller.signal.aborted) return;
          setCatalogs((previous) => ({
            ...previous,
            [provider]: {
              loading: false,
              options: !error && data?.ok ? data.models : [],
              message:
                data?.message ??
                (error ? "モデル一覧を取得できませんでした。" : null),
            },
          }));
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          setCatalogs((previous) => ({
            ...previous,
            [provider]: {
              loading: false,
              options: [],
              message: "モデル一覧を取得できませんでした。",
            },
          }));
        });
    }
    return () => controller.abort();
  }, [selectedProvider]);

  return (
    <div className="space-y-5">
      {error && <InlineNotice tone="danger">{error}</InlineNotice>}
      <div className="space-y-4">
        {CLOUD_MODELS.filter((item) => item.provider === selectedProvider).map(
          ({ provider, label, field }) => {
            const catalog = catalogs[provider];
            const current = form[field] as string;
            const custom = customModels.has(field);
            const includesCurrent = catalog.options.some(
              (option) => option.id === current,
            );
            return (
              <FieldRow key={field} label="モデル">
                <div className="space-y-2">
                  <select
                    className="field"
                    value={custom ? CUSTOM_MODEL : current}
                    aria-label={`${label}モデル`}
                    onChange={(event) => {
                      if (event.target.value === CUSTOM_MODEL) {
                        setCustomModels((previous) =>
                          new Set(previous).add(field),
                        );
                        return;
                      }
                      setCustomModels((previous) => {
                        const next = new Set(previous);
                        next.delete(field);
                        return next;
                      });
                      update(field, event.target.value);
                    }}
                  >
                    {!includesCurrent && !custom && (
                      <option value={current}>{current}</option>
                    )}
                    {catalog.options.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label === option.id
                          ? option.id
                          : `${option.label} (${option.id})`}
                      </option>
                    ))}
                    <option value={CUSTOM_MODEL}>カスタム識別子…</option>
                  </select>
                  {custom && (
                    <input
                      type="text"
                      value={current}
                      onChange={(event) => update(field, event.target.value)}
                      className="field"
                      maxLength={256}
                      aria-label={`${label}カスタムモデル識別子`}
                    />
                  )}
                  <p className="text-xs text-ink-muted">
                    {catalog.loading
                      ? "利用可能なモデルを確認しています。"
                      : (catalog.message ??
                        `${catalog.options.length}件のモデルを選択できます。`)}
                  </p>
                </div>
              </FieldRow>
            );
          },
        )}
        {selectedProvider === "ollama" && (
          <FieldRow label="モデル">
            <input
              type="text"
              value={form.ollamaModel}
              onChange={(event) => update("ollamaModel", event.target.value)}
              className="field"
              maxLength={256}
              aria-label="Ollamaモデル"
            />
          </FieldRow>
        )}
      </div>
      {selectedProvider === "ollama" && (
        <details open={Boolean(error)}>
          <summary className="cursor-pointer text-sm font-medium">
            Ollamaの接続設定
          </summary>
          <div className="space-y-4">
            <FieldRow label="ベースURL" hint="通常は変更不要です">
              <input
                type="url"
                value={form.ollamaBaseUrl}
                onChange={(event) =>
                  update("ollamaBaseUrl", event.target.value)
                }
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
        </details>
      )}
    </div>
  );
}
