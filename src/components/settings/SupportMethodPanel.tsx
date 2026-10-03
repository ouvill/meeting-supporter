import { useState, type ReactNode } from "react";
import type {
  AiAssignableUseCase,
  AiRouteDraftAssignments,
  AiRouteReadModel,
  AiRoutesReloadStatus,
} from "../../hooks/useAiRoutes";
import { Button } from "../ui/Button";
import { InlineNotice } from "../ui/InlineNotice";
import {
  ApiConnectionControl,
  CONNECTIONS,
  CONNECTION_PROVIDER_BY_ROUTE,
  type ConnectionProvider,
} from "./ApiConnectionControl";
import { FieldRow, SettingsSection, ToggleField } from "./SettingsPrimitives";
import { AgentRegistryPanel } from "./AgentRegistryPanel";
import type { ConnectionUiState } from "./types";

interface ConnectionControlBindings {
  connectionStates: Record<ConnectionProvider, ConnectionUiState>;
  secretsStatus: Record<string, boolean>;
  secretInputs: Record<string, string>;
  connectionEditingProvider: ConnectionProvider | null;
  connectionTestingProvider: ConnectionProvider | null;
  connectionTestMessages: Partial<Record<ConnectionProvider, string>>;
  lockedConnectionProviders?: ReadonlySet<ConnectionProvider>;
  onBeginConnectionEdit: (provider: ConnectionProvider) => void;
  onCancelConnectionEdit: (provider: ConnectionProvider) => void;
  onSecretChange: (provider: ConnectionProvider, value: string) => void;
  onTestConnection: (provider: ConnectionProvider) => void;
  onRequestSecretDelete: (provider: ConnectionProvider) => void;
  onCancelSecretDelete: (provider: ConnectionProvider) => void;
}

interface Props extends ConnectionControlBindings {
  agentsLocked?: boolean;
  routes: AiRouteReadModel[];
  assignments: AiRouteDraftAssignments;
  loading: boolean;
  manualReloadStatus: AiRoutesReloadStatus;
  error?: string;
  credentialError?: string;
  replyEnabled: boolean;
  replyAutoGenerate: boolean;
  onAssignmentChange: (
    useCase: AiAssignableUseCase,
    routeId: string | null,
  ) => void;
  onReplyEnabledChange: (enabled: boolean) => void;
  onReplyAutoGenerateChange: (enabled: boolean) => void;
  onReload: () => void;
  modelSettings?: ReactNode;
}

export function dataLocationLabel(value: unknown): string {
  if (value === "local") return "このPC";
  if (value === "cloud") return "クラウド";
  if (value === "external") return "外部サービス";
  return "確認できません";
}

export function billingOwnerLabel(value: unknown): string {
  if (value === "app") return "提供時に料金をご案内（無料ではありません）";
  if (value === "external_subscription") return "利用者の外部契約";
  if (value === "user") return "利用するサービスの従量料金";
  if (value === "none") return "外部サービス料金なし";
  return "確認できません";
}

export function SupportMethodPanel(props: Props) {
  const [managingAgents, setManagingAgents] = useState(false);
  // Hosted accounts are not part of the currently offered product.
  const routes = props.routes.filter(
    (route) => route.kind !== "managed" && route.capabilities.includes("reply"),
  );
  const route = routes.find((route) => route.id === props.assignments.reply);
  const provider =
    route?.kind === "byok" && route.id in CONNECTION_PROVIDER_BY_ROUTE
      ? CONNECTION_PROVIDER_BY_ROUTE[
          route.id as keyof typeof CONNECTION_PROVIDER_BY_ROUTE
        ]
      : null;
  const reloading = props.manualReloadStatus === "loading";
  return (
    <SettingsSection
      title="返答案"
      description="会議中の返答案に使うAIを選びます。"
    >
      <ToggleField
        label="返答案を表示する"
        description="文字起こしだけで使う場合はオフにできます。"
        checked={props.replyEnabled}
        onChange={props.onReplyEnabledChange}
      />
      <FieldRow label="利用するAI">
        <select
          className="field"
          aria-label="返答案に使うAI"
          value={route?.id ?? ""}
          disabled={props.loading}
          onChange={(event) =>
            props.onAssignmentChange("reply", event.target.value || null)
          }
        >
          <option value="">選択してください</option>
          {routes.map((item) => (
            <option
              key={item.id}
              value={item.id}
              disabled={
                !item.selectable ||
                item.availability === "planned" ||
                item.readiness === "not_offered"
              }
            >
              {item.label}
              {item.readiness === "ready"
                ? ""
                : item.readiness === "setup_required"
                  ? "（設定が必要）"
                  : "（利用できません）"}
            </option>
          ))}
        </select>
      </FieldRow>
      {props.loading && (
        <p role="status" className="text-sm text-ink-muted">
          利用状態を確認しています
        </p>
      )}
      {props.assignments.reply && !route && !props.loading && (
        <InlineNotice tone="warning">
          選択していたAIは現在利用できません。別のAIを選んでください。
        </InlineNotice>
      )}
      {route && (
        <div data-route-id={route.id} className="space-y-4">
          {props.modelSettings}
          <FieldRow label="利用状態">
            <div className="space-y-1 text-sm">
              <p>
                {route.readiness === "ready"
                  ? "利用できます"
                  : route.readiness === "setup_required"
                    ? "設定が必要です"
                    : "現在は利用できません"}
                {route.availability === "experimental" && (
                  <span className="ml-2 text-xs text-ink-muted">試験提供</span>
                )}
              </p>
              {route.readiness !== "ready" && route.message && (
                <p className="text-ink-muted">{route.message}</p>
              )}
            </div>
          </FieldRow>
          <FieldRow label="データと費用">
            <p className="text-sm text-ink-muted">
              <span>{dataLocationLabel(route.data_location)}</span> ·{" "}
              <span>{billingOwnerLabel(route.billing_owner)}</span>
            </p>
          </FieldRow>
          {provider && (
            <ApiConnectionControl
              provider={provider}
              state={props.connectionStates[provider]}
              hasSavedKey={
                props.secretsStatus[CONNECTIONS[provider].secretKey] ?? false
              }
              draftKey={
                props.secretInputs[CONNECTIONS[provider].secretKey] ?? ""
              }
              editing={props.connectionEditingProvider === provider}
              testing={props.connectionTestingProvider === provider}
              disabled={
                props.lockedConnectionProviders?.has(provider) ||
                (props.connectionTestingProvider !== null &&
                  props.connectionTestingProvider !== provider)
              }
              testMessage={props.connectionTestMessages[provider] ?? null}
              onBeginEdit={() => props.onBeginConnectionEdit(provider)}
              onCancelEdit={() => props.onCancelConnectionEdit(provider)}
              onDraftChange={(value) => props.onSecretChange(provider, value)}
              onTest={() => props.onTestConnection(provider)}
              onRequestDelete={() => props.onRequestSecretDelete(provider)}
              onCancelDelete={() => props.onCancelSecretDelete(provider)}
            />
          )}
        </div>
      )}
      {props.credentialError && (
        <InlineNotice tone="danger">{props.credentialError}</InlineNotice>
      )}
      {props.error && <InlineNotice tone="danger">{props.error}</InlineNotice>}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="quiet"
          size="sm"
          onClick={props.onReload}
          disabled={reloading}
        >
          {reloading ? "確認中…" : "接続状態を再確認"}
        </Button>
        <Button
          variant="quiet"
          size="sm"
          onClick={() => setManagingAgents((value) => !value)}
          aria-expanded={managingAgents}
          aria-controls="agent-connections"
        >
          エージェントを追加・管理
        </Button>
        {props.manualReloadStatus === "success" && (
          <span role="status" className="text-xs text-ink-muted">
            状態を更新しました
          </span>
        )}
        {props.manualReloadStatus === "error" && (
          <span role="status" className="text-xs text-danger">
            更新できませんでした
          </span>
        )}
      </div>
      {managingAgents && (
        <div id="agent-connections">
          <AgentRegistryPanel
            locked={props.agentsLocked ?? false}
            onChanged={props.onReload}
          />
        </div>
      )}
      <details className="border-t border-line pt-4">
        <summary className="cursor-pointer text-sm font-medium">
          返答案の動作
        </summary>
        <div className="pt-4">
          <ToggleField
            label="発話ごとに自動で作る"
            description="オフの場合は、必要なときに手動で作ります。自動生成は利用回数が増えます。"
            checked={props.replyAutoGenerate}
            disabled={!props.replyEnabled}
            onChange={props.onReplyAutoGenerateChange}
          />
        </div>
      </details>
    </SettingsSection>
  );
}
