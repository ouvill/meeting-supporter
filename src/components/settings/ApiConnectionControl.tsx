import { Eye, EyeOff, KeyRound, Trash2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Button } from "../ui/Button";
import { InlineNotice } from "../ui/InlineNotice";
import { Status, type StatusTone } from "../ui/Status";
import type { ConnectionUiState } from "./types";

export const CONNECTIONS = {
  openai: {
    secretKey: "OPENAI_API_KEY",
    label: "OpenAI",
    usage: "クラウド音声認識とAI機能",
  },
  deepgram: {
    secretKey: "DEEPGRAM_API_KEY",
    label: "Deepgram",
    usage: "クラウド音声認識",
  },
  xai: {
    secretKey: "XAI_API_KEY",
    label: "Grok / xAI",
    usage: "クラウド音声認識",
  },
  gemini: {
    secretKey: "GEMINI_API_KEY",
    label: "Google Gemini",
    usage: "AI機能",
  },
  anthropic: {
    secretKey: "ANTHROPIC_API_KEY",
    label: "Anthropic",
    usage: "AI機能",
  },
} as const;

export type ConnectionProvider = keyof typeof CONNECTIONS;
export type ConnectionSecretKey =
  (typeof CONNECTIONS)[ConnectionProvider]["secretKey"];
export type ConnectionVerification = "verified" | "unverified" | "failed";

export const CONNECTION_PROVIDER_BY_ROUTE = {
  gemini: "gemini",
  openai: "openai",
  anthropic: "anthropic",
} as const satisfies Record<string, ConnectionProvider>;

export interface ApiConnectionControlProps {
  provider: ConnectionProvider;
  state: ConnectionUiState;
  hasSavedKey: boolean;
  draftKey: string;
  editing: boolean;
  testing: boolean;
  disabled?: boolean;
  testMessage: string | null;
  onBeginEdit: () => void;
  onCancelEdit: () => void;
  onDraftChange: (value: string) => void;
  onTest: () => void;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
}

const DIALOG_STATE: Record<
  ConnectionUiState,
  { label: string; tone: StatusTone }
> = {
  unconfigured: { label: "未設定", tone: "neutral" },
  "draft-unverified": { label: "入力済み・未保存", tone: "warning" },
  "saved-unverified": { label: "保存済み・未確認", tone: "warning" },
  verified: { label: "接続確認済み", tone: "positive" },
  failed: { label: "接続に失敗", tone: "danger" },
  "pending-delete": { label: "削除予定", tone: "warning" },
};

export function ApiConnectionControl({
  provider,
  state,
  hasSavedKey,
  draftKey,
  editing,
  testing,
  disabled = false,
  testMessage,
  onBeginEdit,
  onCancelEdit,
  onDraftChange,
  onTest,
  onRequestDelete,
  onCancelDelete,
}: ApiConnectionControlProps) {
  const [showSecret, setShowSecret] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const inputId = useId();
  const helpId = useId();
  const connection = CONNECTIONS[provider];
  const expanded = !hasSavedKey || editing || draftKey.trim().length > 0;
  useEffect(() => {
    if (!expanded || state === "pending-delete") setShowSecret(false);
  }, [expanded, state]);
  const status = testing
    ? { label: "確認中", tone: "busy" as const }
    : DIALOG_STATE[state];
  const testLabel =
    state === "failed"
      ? "もう一度接続を確認"
      : state === "verified"
        ? "接続を再確認"
        : "接続を確認";

  return (
    <section
      aria-label={`${connection.label} API接続`}
      className="mt-4 space-y-3 rounded-xl border border-line bg-surface p-3.5 shadow-card"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary">
          <KeyRound className="size-4" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-ink">
            {connection.label} API接続
          </p>
          <p className="text-xs text-ink-muted">{connection.usage}</p>
        </div>
        <Status tone={status.tone} aria-live="polite">
          {status.label}
        </Status>
      </div>

      {state === "pending-delete" ? (
        <InlineNotice tone="warning" title="APIキーは削除予定です">
          <p>
            画面下部の「保存」を押すと削除され、この接続を利用する設定は使えなくなります。
          </p>
          <Button
            size="sm"
            variant="secondary"
            onClick={onCancelDelete}
            disabled={testing}
            aria-label={`${connection.label} APIキーの削除を取り消す`}
            className="mt-3"
          >
            削除を取り消す
          </Button>
        </InlineNotice>
      ) : expanded ? (
        <div className="space-y-2">
          <label
            htmlFor={inputId}
            className="block text-sm font-semibold text-ink"
          >
            APIキー
          </label>
          <div className="flex gap-2">
            <input
              id={inputId}
              aria-label={`${connection.label} APIキー`}
              aria-describedby={helpId}
              type={showSecret ? "text" : "password"}
              autoComplete="off"
              value={draftKey}
              onChange={(event) => onDraftChange(event.target.value)}
              placeholder="APIキーを入力"
              disabled={testing}
              className="field min-w-0 flex-1"
            />
            <Button
              size="md"
              variant="secondary"
              onClick={() => setShowSecret((value) => !value)}
              disabled={testing}
              aria-label={showSecret ? "APIキーを隠す" : "APIキーを表示"}
              aria-pressed={showSecret}
              className="px-3"
            >
              {showSecret ? (
                <EyeOff className="size-4" aria-hidden="true" />
              ) : (
                <Eye className="size-4" aria-hidden="true" />
              )}
            </Button>
          </div>
          <p id={helpId} className="text-xs leading-relaxed text-ink-muted">
            入力値は、接続確認または設定保存のときだけ送信します。保存済みのキーは再表示しません。
          </p>
          {hasSavedKey && editing && (
            <Button
              size="sm"
              variant="quiet"
              onClick={() => {
                setShowSecret(false);
                onCancelEdit();
              }}
              disabled={testing}
            >
              変更をキャンセル
            </Button>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-3 rounded-lg bg-surface-muted p-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-ink-muted">
            保存済みのAPIキーを使用します。値は安全のため表示しません。
          </p>
          <Button
            size="md"
            variant="secondary"
            onClick={onBeginEdit}
            disabled={testing}
            aria-label={`${connection.label} APIキーを変更`}
            className="w-full max-sm:min-h-11 sm:w-auto"
          >
            APIキーを変更
          </Button>
        </div>
      )}

      {confirmingDelete && state !== "pending-delete" && (
        <InlineNotice tone="danger" title="保存済みのAPIキーを削除しますか？">
          <p>この操作は画面下部の「保存」後に反映されます。</p>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <Button
              size="md"
              variant="danger"
              aria-label={`${connection.label} APIキーを削除予定にする`}
              onClick={() => {
                onRequestDelete();
                setConfirmingDelete(false);
              }}
              disabled={testing}
              className="w-full max-sm:min-h-11 sm:w-auto"
            >
              削除予定にする
            </Button>
            <Button
              size="md"
              variant="secondary"
              onClick={() => setConfirmingDelete(false)}
              disabled={testing}
              className="w-full max-sm:min-h-11 sm:w-auto"
            >
              キャンセル
            </Button>
          </div>
        </InlineNotice>
      )}

      {testMessage && (
        <InlineNotice tone={state === "failed" ? "danger" : "positive"}>
          {testMessage}
        </InlineNotice>
      )}

      {!confirmingDelete && state !== "pending-delete" && (
        <div className="flex flex-col gap-2 border-t border-line pt-3 sm:flex-row sm:items-center">
          <Button
            size="md"
            variant={state === "verified" ? "secondary" : "primary"}
            aria-label={`${connection.label} ${testing ? "確認中" : testLabel}`}
            onClick={onTest}
            disabled={
              disabled || testing || (!hasSavedKey && !draftKey.trim())
            }
            loading={testing}
            className="w-full max-sm:min-h-11 sm:w-auto"
          >
            {testing ? "確認中…" : testLabel}
          </Button>
          {hasSavedKey && (
            <Button
              size="md"
              variant="quiet"
              onClick={() => setConfirmingDelete(true)}
              disabled={testing}
              aria-label={`${connection.label} APIキーを削除`}
              className="w-full text-danger max-sm:min-h-11 sm:ml-auto sm:w-auto"
            >
              <Trash2 className="size-4" aria-hidden="true" />
              削除
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
