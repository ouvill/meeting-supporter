import { useState, type ReactNode } from "react";
import {
  ChevronDown,
  CircleAlert,
  CircleCheck,
  LoaderCircle,
  Plus,
} from "lucide-react";
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
  connectionRouteId: string | null;
  onConnectionRouteChange: (routeId: string | null) => void;
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
  const [addingAgent, setAddingAgent] = useState(false);
  const { connectionRouteId, onConnectionRouteChange: setConnectionRouteId } =
    props;
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
  const agentId = route?.id.startsWith("acp:") ? route.id.slice(4) : null;
  const connectionLabel: Record<ConnectionUiState, string> = {
    unconfigured: "APIキー未設定",
    "draft-unverified": "APIキー入力済み・未保存",
    "saved-unverified": "APIキー設定済み・接続未確認",
    verified: "接続確認済み",
    failed: "接続に失敗",
    "pending-delete": "APIキー削除予定",
  };
  // Whatever still needs the user's input stays visible without a toggle.
  const needsConnectionSetup =
    route?.readiness === "setup_required" ||
    (provider !== null &&
      route?.readiness === "ready" &&
      !["verified", "saved-unverified"].includes(
        props.connectionStates[provider],
      ));
  const connectionOpen =
    route !== undefined &&
    (needsConnectionSetup || route.id === connectionRouteId);
  const statusLabel = !route
    ? null
    : provider && ["ready", "setup_required"].includes(route.readiness)
      ? connectionLabel[props.connectionStates[provider]]
      : route.readiness === "ready"
        ? "利用できます"
        : route.readiness === "setup_required"
          ? "設定が必要です"
          : "現在は利用できません";
  const statusReady =
    route?.readiness === "ready" &&
    (!provider || props.connectionStates[provider] === "verified");
  return (
    <div className="space-y-8">
      <ToggleField
        label="返答案を表示する"
        description="オフにすると、録音と文字起こしだけを行います。"
        checked={props.replyEnabled}
        onChange={props.onReplyEnabledChange}
      />

      <SettingsSection
        title="使うAI"
        description="返答案を作るAIサービスを選びます。APIキーやログインが必要な場合は、この下に入力欄が表示されます。"
      >
        <FieldRow label="AIサービス">
          <div className="space-y-2">
            <select
              className="field"
              aria-label="返答案に使うAI"
              value={route?.id ?? ""}
              disabled={props.loading}
              onChange={(event) => {
                setConnectionRouteId(null);
                props.onAssignmentChange("reply", event.target.value || null);
              }}
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
            {props.loading ? (
              <p
                role="status"
                className="flex items-center gap-1.5 text-sm text-ink-muted"
              >
                <LoaderCircle
                  aria-hidden="true"
                  className="size-4 animate-spin motion-reduce:animate-none"
                />
                利用状態を確認しています
              </p>
            ) : (
              route &&
              statusLabel && (
                <div data-route-status className="space-y-1 text-sm">
                  <p
                    className={`flex items-center gap-1.5 font-medium ${statusReady ? "text-positive" : "text-warning"}`}
                  >
                    {statusReady ? (
                      <CircleCheck
                        aria-hidden="true"
                        className="size-4 shrink-0"
                      />
                    ) : (
                      <CircleAlert
                        aria-hidden="true"
                        className="size-4 shrink-0"
                      />
                    )}
                    {statusLabel}
                    {route.availability === "experimental" && (
                      <span className="ml-1 text-xs font-normal text-ink-muted">
                        試験提供
                      </span>
                    )}
                  </p>
                  {route.readiness !== "ready" && route.message && (
                    <p className="text-ink-muted">{route.message}</p>
                  )}
                </div>
              )
            )}
            <div className="flex flex-wrap items-center gap-x-2">
              <Button
                variant="quiet"
                size="sm"
                onClick={() => setAddingAgent((value) => !value)}
                aria-expanded={addingAgent}
                aria-controls="agent-catalog"
                className="-ml-3"
              >
                <Plus aria-hidden="true" className="size-4" />
                AIエージェントを追加
              </Button>
              <span className="text-xs text-ink-muted">
                一覧にないAIを使いたい場合
              </span>
            </div>
          </div>
        </FieldRow>
        {addingAgent && (
          <div id="agent-catalog">
            <AgentRegistryPanel
              locked={props.agentsLocked ?? false}
              onChanged={props.onReload}
              onSelect={(id) => {
                props.onAssignmentChange("reply", `acp:${id}`);
                setConnectionRouteId(`acp:${id}`);
                setAddingAgent(false);
              }}
            />
          </div>
        )}
        {props.assignments.reply && !route && !props.loading && (
          <InlineNotice tone="warning">
            選択していたAIは現在利用できません。別のAIを選んでください。
          </InlineNotice>
        )}
        {props.error && (
          <InlineNotice tone="danger">
            <p>{props.error}</p>
            <Button
              variant="secondary"
              size="sm"
              onClick={props.onReload}
              disabled={reloading}
              className="mt-3"
            >
              {reloading ? "確認中…" : "再試行"}
            </Button>
          </InlineNotice>
        )}
        {route && (
          <div data-route-id={route.id} className="space-y-4">
            {props.modelSettings}
            {(provider || agentId) && (
              <FieldRow label={provider ? "APIキー" : "接続とログイン"}>
                <div className="space-y-3">
                  {!needsConnectionSetup && (
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                      <Button
                        variant="secondary"
                        size="sm"
                        aria-expanded={connectionOpen}
                        aria-controls="selected-ai-connection"
                        onClick={() =>
                          setConnectionRouteId(connectionOpen ? null : route.id)
                        }
                      >
                        接続設定
                        <ChevronDown
                          aria-hidden="true"
                          className={`size-4 transition-transform motion-reduce:transition-none ${connectionOpen ? "rotate-180" : ""}`}
                        />
                      </Button>
                      {props.credentialError && !connectionOpen && (
                        <p className="text-sm text-ink-muted">
                          「接続設定」を開いて、
                          {provider ? "APIキーを設定" : "ログイン"}
                          してください。
                        </p>
                      )}
                    </div>
                  )}
                  {connectionOpen && (
                    <div id="selected-ai-connection">
                      {provider ? (
                        <ApiConnectionControl
                          provider={provider}
                          state={props.connectionStates[provider]}
                          hasSavedKey={
                            props.secretsStatus[
                              CONNECTIONS[provider].secretKey
                            ] ?? false
                          }
                          draftKey={
                            props.secretInputs[
                              CONNECTIONS[provider].secretKey
                            ] ?? ""
                          }
                          editing={props.connectionEditingProvider === provider}
                          testing={props.connectionTestingProvider === provider}
                          disabled={
                            props.lockedConnectionProviders?.has(provider) ||
                            (props.connectionTestingProvider !== null &&
                              props.connectionTestingProvider !== provider)
                          }
                          testMessage={
                            props.connectionTestMessages[provider] ?? null
                          }
                          onBeginEdit={() =>
                            props.onBeginConnectionEdit(provider)
                          }
                          onCancelEdit={() =>
                            props.onCancelConnectionEdit(provider)
                          }
                          onDraftChange={(value) =>
                            props.onSecretChange(provider, value)
                          }
                          onTest={() => props.onTestConnection(provider)}
                          onRequestDelete={() =>
                            props.onRequestSecretDelete(provider)
                          }
                          onCancelDelete={() =>
                            props.onCancelSecretDelete(provider)
                          }
                        />
                      ) : agentId ? (
                        <AgentRegistryPanel
                          key={agentId}
                          agentId={agentId}
                          locked={props.agentsLocked ?? false}
                          onChanged={props.onReload}
                        />
                      ) : null}
                    </div>
                  )}
                </div>
              </FieldRow>
            )}
          </div>
        )}
        {props.credentialError && (
          <InlineNotice tone="danger">{props.credentialError}</InlineNotice>
        )}
      </SettingsSection>

      <SettingsSection title="返答案の作り方">
        <ToggleField
          label="自動で返答案を作る"
          description="オフの場合は、必要なときに手動で作ります。自動生成は利用回数が増えます。"
          checked={props.replyAutoGenerate}
          disabled={!props.replyEnabled}
          onChange={props.onReplyAutoGenerateChange}
        />
      </SettingsSection>

      {route && (
        <SettingsSection
          title="データの送信先と費用"
          description="選んだAIサービスに、返答案の作成に必要な会議のテキストを送ります。"
        >
          <dl className="grid gap-x-4 gap-y-2 text-sm md:grid-cols-[10rem_minmax(0,1fr)]">
            <dt className="font-medium text-ink">処理する場所</dt>
            <dd className="text-ink-muted">
              {dataLocationLabel(route.data_location)}
            </dd>
            <dt className="font-medium text-ink">費用</dt>
            <dd className="text-ink-muted">
              {billingOwnerLabel(route.billing_owner)}
            </dd>
          </dl>
        </SettingsSection>
      )}
    </div>
  );
}
