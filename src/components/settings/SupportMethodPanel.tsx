import {
  Check,
  CircleAlert,
  ExternalLink,
  KeyRound,
  Laptop,
  LoaderCircle,
  Network,
  RefreshCw,
  Sparkles,
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
import { SettingsCard, SettingsPage, ToggleField } from "./SettingsPrimitives";
import type { ConnectionUiState } from "./types";

interface ConnectionControlBindings {
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
}

interface Props extends ConnectionControlBindings {
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
  onRouteAction: (route: AiRouteReadModel) => void;
  onReload: () => void;
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
  if (value === "user") return "利用者";
  if (value === "none") return "外部サービス料金なし";
  return "確認できません";
}

function stateLabel(route: AiRouteReadModel) {
  if (route.readiness === "ready") return "利用できます";
  if (route.readiness === "setup_required") return "設定が必要";
  if (route.availability === "planned") return "準備中";
  return "現在は利用できません";
}

function routeActionLabel(action: AiRouteReadModel["action"]): string | null {
  if (action === "install") return "Codex CLIの入手方法を見る";
  if (action === "login" || action === "sign_in") return "ログイン";
  if (action === "subscribe") return "月額プランを申し込む";
  if (action === "manage_billing") return "支払いを確認";
  if (action === "view_usage") return "利用枠を確認";
  if (action === "retry") return "もう一度確認";
  return null;
}

function routeIcon(route: AiRouteReadModel) {
  if (route.id === "managed") return Sparkles;
  if (route.id === "acp") return Network;
  if (route.kind === "local") return Laptop;
  if (route.kind === "byok") return KeyRound;
  return KeyRound;
}

const USE_CASE_OPTIONS: ReadonlyArray<{
  useCase: AiAssignableUseCase;
  label: string;
}> = [
  { useCase: "reply", label: "返答案" },
  { useCase: "info", label: "会話メモ" },
  { useCase: "minutes", label: "要約・議事録" },
];

function RouteCard({
  route,
  assignments,
  reloading,
  onAssignmentChange,
  onRouteAction,
  onReload,
  connection,
  credentialError,
}: {
  route: AiRouteReadModel;
  assignments: AiRouteDraftAssignments;
  reloading: boolean;
  onAssignmentChange: (
    useCase: AiAssignableUseCase,
    routeId: string | null,
  ) => void;
  onRouteAction: () => void;
  onReload: () => void;
  connection: ConnectionControlBindings;
  credentialError?: string;
}) {
  const Icon = routeIcon(route);
  const actionLabel = routeActionLabel(route.action);
  const provider =
    route.kind === "byok" && route.id in CONNECTION_PROVIDER_BY_ROUTE
      ? CONNECTION_PROVIDER_BY_ROUTE[
          route.id as keyof typeof CONNECTION_PROVIDER_BY_ROUTE
        ]
      : null;
  const selectionOffered =
    route.selectable &&
    (route.readiness === "ready" ||
      route.kind === "byok" ||
      route.kind === "local" ||
      route.id === "acp");
  const offeredUseCases = USE_CASE_OPTIONS.filter(({ useCase }) =>
    route.capabilities.includes(useCase),
  );
  const selectedUseCases = offeredUseCases.filter(
    ({ useCase }) => assignments[useCase] === route.id,
  );
  const selected = selectedUseCases.length > 0;

  return (
    <div
      data-route-id={route.id}
      className={`rounded-xl border p-3.5 ${selected ? "border-primary bg-primary-soft ring-1 ring-primary/15" : "border-line bg-surface"}`}
    >
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${selected ? "bg-primary text-white" : "bg-paper text-ink-muted"}`}
        >
          {selected ? (
            <Check className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Icon className="h-4 w-4" aria-hidden="true" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h5 className="font-display text-sm font-bold text-ink">
              {route.id === "managed"
                ? "アプリにおまかせ"
                : route.id === "codex"
                  ? "ChatGPT の契約を使う"
                  : route.label}
            </h5>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-semibold ${route.readiness === "ready" ? "bg-positive-soft text-positive" : "bg-warning-soft text-warning"}`}
            >
              {stateLabel(route)}
            </span>
          </div>
          <p className="mt-1 text-sm leading-relaxed text-ink-muted">
            {route.description}
          </p>
          {route.message && route.readiness !== "ready" && (
            <p className="mt-2 text-sm text-ink">{route.message}</p>
          )}
          <dl className="mt-3 grid gap-1 text-sm text-ink-muted sm:grid-cols-2">
            <div className="flex gap-2">
              <dt className="font-semibold text-ink">処理場所</dt>
              <dd>{dataLocationLabel(route.data_location)}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="font-semibold text-ink">費用負担</dt>
              <dd>{billingOwnerLabel(route.billing_owner)}</dd>
            </div>
          </dl>
          {offeredUseCases.length > 0 && (
            <div
              className="mt-3 flex flex-wrap items-center gap-2"
              aria-label={`${route.label} の用途`}
            >
              {offeredUseCases.map(({ useCase, label }) => {
                const pressed = assignments[useCase] === route.id;
                return (
                  <Button
                    key={useCase}
                    size="sm"
                    variant={pressed ? "primary" : "secondary"}
                    aria-pressed={pressed}
                    onClick={() =>
                      onAssignmentChange(useCase, pressed ? null : route.id)
                    }
                    disabled={!pressed && !selectionOffered}
                  >
                    {label}
                  </Button>
                );
              })}
            </div>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {actionLabel && (
              <Button size="sm" variant="primary" onClick={onRouteAction}>
                {actionLabel}
                {route.action !== "retry" && route.action !== "view_usage" && (
                  <ExternalLink className="h-3 w-3" aria-hidden="true" />
                )}
              </Button>
            )}
            {route.readiness !== "ready" && (
              <Button
                size="sm"
                variant="quiet"
                onClick={onReload}
                disabled={reloading}
              >
                <RefreshCw
                  className={`h-3 w-3 ${reloading ? "animate-spin" : ""}`}
                  aria-hidden="true"
                />
                状態を再確認
              </Button>
            )}
          </div>
          {provider && (
            <ApiConnectionControl
              provider={provider}
              state={connection.connectionStates[provider]}
              hasSavedKey={
                connection.secretsStatus[CONNECTIONS[provider].secretKey] ??
                false
              }
              draftKey={
                connection.secretInputs[CONNECTIONS[provider].secretKey] ?? ""
              }
              editing={connection.connectionEditingProvider === provider}
              testing={connection.connectionTestingProvider === provider}
              disabled={
                connection.connectionTestingProvider !== null &&
                connection.connectionTestingProvider !== provider
              }
              testMessage={connection.connectionTestMessages[provider] ?? null}
              onBeginEdit={() => connection.onBeginConnectionEdit(provider)}
              onCancelEdit={() => connection.onCancelConnectionEdit(provider)}
              onDraftChange={(value) =>
                connection.onSecretChange(provider, value)
              }
              onTest={() => connection.onTestConnection(provider)}
              onRequestDelete={() => connection.onRequestSecretDelete(provider)}
              onCancelDelete={() => connection.onCancelSecretDelete(provider)}
            />
          )}
          {provider && selected && credentialError && (
            <InlineNotice tone="danger">{credentialError}</InlineNotice>
          )}
        </div>
      </div>
    </div>
  );
}

export function SupportMethodPanel({
  routes,
  assignments,
  loading,
  manualReloadStatus,
  error,
  credentialError,
  replyEnabled,
  replyAutoGenerate,
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
  onAssignmentChange,
  onReplyEnabledChange,
  onReplyAutoGenerateChange,
  onRouteAction,
  onReload,
}: Props) {
  const generalRoutes = routes.filter(
    (route) => route.kind === "managed" || route.kind === "subscription_app",
  );
  const setupRoutes = routes.filter((route) => !generalRoutes.includes(route));
  const isManualReloading = manualReloadStatus === "loading";
  const credentialErrorHandledInline = routes.some(
    (route) =>
      Object.values(assignments).includes(route.id) &&
      route.kind === "byok" &&
      route.id in CONNECTION_PROVIDER_BY_ROUTE,
  );
  const connection: ConnectionControlBindings = {
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
  };
  const renderRoutes = (items: AiRouteReadModel[]) => (
    <div className="space-y-2.5">
      {items.map((route) => (
        <RouteCard
          key={route.id}
          route={route}
          assignments={assignments}
          reloading={isManualReloading}
          connection={connection}
          credentialError={
            Object.values(assignments).includes(route.id)
              ? credentialError
              : undefined
          }
          onAssignmentChange={onAssignmentChange}
          onRouteAction={() => onRouteAction(route)}
          onReload={onReload}
        />
      ))}
    </div>
  );
  return (
    <SettingsPage
      title="支援方法"
      description="APIキーが必要な方法は、各カード内で設定できます。"
    >
      <SettingsCard title="AI機能の割り当て">
        {loading && !routes.length ? (
          <div
            className="flex items-center justify-center gap-2 py-10 text-xs text-ink-faint"
            role="status"
          >
            <LoaderCircle className="h-4 w-4 animate-spin" />
            利用状態を確認しています
          </div>
        ) : error && !routes.length ? (
          <div
            className="rounded-xl bg-danger-soft p-4 text-xs text-danger"
            role="alert"
          >
            <CircleAlert className="mr-2 inline h-4 w-4" />
            {error}
            {manualReloadStatus === "error" && (
              <p className="mt-2" role="status">
                更新できませんでした
              </p>
            )}
            <button
              type="button"
              onClick={onReload}
              className="ml-2 font-semibold underline"
            >
              再確認する
            </button>
          </div>
        ) : (
          <div className="space-y-5">
            {manualReloadStatus === "loading" && (
              <div
                className="flex items-center gap-2 text-xs text-ink-faint"
                role="status"
              >
                <LoaderCircle className="h-4 w-4 animate-spin" />
                状態を更新しています
              </div>
            )}
            {manualReloadStatus === "success" && (
              <p className="text-xs text-positive" role="status">
                状態を更新しました
              </p>
            )}
            {manualReloadStatus === "error" && (
              <p className="text-xs text-danger" role="status">
                更新できませんでした
              </p>
            )}
            <section aria-labelledby="general-routes">
              <h4
                id="general-routes"
                className="mb-2 text-[11px] font-bold text-ink-muted"
              >
                一般
              </h4>
              {renderRoutes(generalRoutes)}
            </section>
            <section aria-labelledby="setup-routes">
              <h4
                id="setup-routes"
                className="mb-2 text-[11px] font-bold text-ink-muted"
              >
                要設定
              </h4>
              {renderRoutes(setupRoutes)}
            </section>
            {credentialError && !credentialErrorHandledInline && (
              <InlineNotice tone="danger">{credentialError}</InlineNotice>
            )}
            {error && (
              <p className="text-[11px] font-medium text-danger" role="alert">
                {error}
              </p>
            )}
          </div>
        )}
      </SettingsCard>
      <SettingsCard title="返答案の表示">
        <div className="divide-y divide-line">
          <div className="pb-4">
            <ToggleField
              label="返答案を表示する"
              description="会議中、話の流れに合わせた返答案を表示します。"
              checked={replyEnabled}
              onChange={onReplyEnabledChange}
            />
          </div>
          <div className="pt-4">
            <ToggleField
              label="発話ごとに自動で作る"
              description="発話が確定するたびに返答案を作ります。利用回数が増えるため、必要な場合だけ有効にしてください。"
              checked={replyAutoGenerate}
              disabled={!replyEnabled}
              onChange={onReplyAutoGenerateChange}
            />
          </div>
        </div>
      </SettingsCard>
    </SettingsPage>
  );
}
