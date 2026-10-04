import { useEffect, useId, useState } from "react";
import { getOllamaModelsApiSettingsOllamaModelsGet } from "../../api/generated/sdk.gen";
import { Button } from "../ui/Button";
import { InlineNotice } from "../ui/InlineNotice";
import { FieldRow } from "./SettingsPrimitives";

interface Props {
  baseUrl: string;
  model: string;
  onBaseUrlChange: (value: string) => void;
  onModelChange: (value: string) => void;
}

type ModelCatalog = {
  baseUrl: string;
  ok: boolean;
  models: string[];
};

export function OllamaModelSettings({
  baseUrl,
  model,
  onBaseUrlChange,
  onModelChange,
}: Props) {
  const url = baseUrl.trim();
  const messageId = useId();
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    setCatalog(null);
    if (!url) return;
    const controller = new AbortController();
    // Wait for URL typing to pause and discard responses from previous URLs.
    const timer = window.setTimeout(async () => {
      try {
        const { data, error } = await getOllamaModelsApiSettingsOllamaModelsGet(
          {
            query: { base_url: url },
            signal: controller.signal,
          },
        );
        if (controller.signal.aborted) return;
        setCatalog({
          baseUrl: url,
          ok: !error && data?.ok === true,
          models:
            !error && data?.ok
              ? [...new Set(data.models.filter((name) => name.trim()))].sort()
              : [],
        });
      } catch {
        if (controller.signal.aborted) return;
        setCatalog({ baseUrl: url, ok: false, models: [] });
      }
    }, 300);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [url, reload]);

  const currentCatalog = catalog?.baseUrl === url ? catalog : null;
  const loading = Boolean(url) && currentCatalog === null;
  const failed = currentCatalog?.ok === false;
  const models = currentCatalog?.models ?? [];
  const missingModel = currentCatalog?.ok && model && !models.includes(model);
  const message = !url
    ? "ベースURLを入力してください。"
    : loading
      ? "モデル一覧を取得しています。"
      : failed
        ? "モデル一覧を取得できませんでした。URLとOllamaの起動状態を確認して、再取得してください。"
        : models.length === 0
          ? "接続できましたが、モデルがありません。Ollamaでモデルを追加してから、一覧を更新してください。"
          : missingModel
            ? "選択中のモデルが接続先に見つかりません。一覧から選び直してください。"
            : `${models.length}件のモデルから選択できます。`;

  return (
    <div className="space-y-4">
      <FieldRow label="ベースURL" hint="通常は変更不要です">
        <input
          type="url"
          value={baseUrl}
          onChange={(event) => onBaseUrlChange(event.target.value)}
          placeholder="http://localhost:11434/v1"
          className="field"
          aria-label="OllamaベースURL"
        />
      </FieldRow>
      <FieldRow label="モデル">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={model}
              onChange={(event) => onModelChange(event.target.value)}
              disabled={loading || models.length === 0}
              className="field min-w-0 flex-1"
              aria-label="Ollamaモデル"
              aria-describedby={messageId}
              aria-busy={loading}
            >
              {!models.includes(model) && (
                <option value={model} disabled>
                  {model
                    ? `${model}（${currentCatalog?.ok ? "一覧にありません" : "未確認"}）`
                    : "モデルを選択してください"}
                </option>
              )}
              {models.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <Button
              size="sm"
              variant="secondary"
              loading={loading}
              disabled={!url}
              onClick={() => {
                setCatalog(null);
                setReload((value) => value + 1);
              }}
            >
              モデル一覧を更新
            </Button>
          </div>
          <p
            id={messageId}
            className={`text-xs ${failed ? "text-danger" : "text-ink-muted"}`}
            role={failed ? "alert" : "status"}
          >
            {message}
          </p>
        </div>
      </FieldRow>
      <InlineNotice tone="warning">
        localhost / 127.0.0.1 / ::1
        以外のURLを指定すると、会議テキストが外部へ送信される可能性があります。
      </InlineNotice>
    </div>
  );
}
