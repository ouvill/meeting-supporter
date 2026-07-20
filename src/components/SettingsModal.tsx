import { useState } from "react";
import { CircleAlert } from "lucide-react";
import type { AiRoutesController } from "../hooks/useAiRoutes";
import { useManagedSttAvailability } from "../hooks/useManagedService";
import { Dialog, DialogContent } from "./ui/Dialog";
import { Button } from "./ui/Button";
import { AccountSettingsPanel } from "./settings/AccountSettingsPanel";
import { AdvancedSettingsPanel } from "./settings/AdvancedSettingsPanel";
import { AboutSettingsPanel } from "./settings/AboutSettingsPanel";
import { AudioSettingsPanel } from "./settings/AudioSettingsPanel";
import { PrivacySettingsPanel } from "./settings/PrivacySettingsPanel";
import { SettingsNavigation } from "./settings/SettingsPrimitives";
import { SupportMethodPanel } from "./settings/SupportMethodPanel";
import type { SettingsCategory } from "./settings/types";
import { useSettingsForm } from "./settings/useSettingsForm";

interface Props {
  onClose: () => void;
  routes: AiRoutesController;
  restoreFocusTo?: HTMLElement | null;
}

const CATEGORY_LABELS: Record<SettingsCategory, string> = {
  account: "アカウントとプラン",
  support: "支援方法",
  audio: "音声",
  privacy: "データとプライバシー",
  advanced: "詳細設定",
  about: "このアプリについて",
};
export function SettingsModal({ onClose, routes, restoreFocusTo }: Props) {
  const controller = useSettingsForm({ routes });
  const {
    form,
    activeCategory,
    setActiveCategory,
    loaded,
    loadingError,
    fieldErrors,
    sectionError,
    saveMessage,
    clearSaveMessage,
    busy,
    dirty,
    ollamaTesting,
    ollamaMessage,
    ollamaMessageIsError,
    connectionEditingProvider,
    connectionTestingProvider,
    connectionTestMessages,
    speechModel,
    selectedRoute,
    connectionStates,
    updateForm,
    updateSecret,
    beginConnectionEdit,
    cancelConnectionEdit,
    testConnection,
    scheduleSecretDeletion,
    cancelSecretDeletion,
    assignRoute,
    chooseContextDirectory,
    handleRouteAction,
    testOllamaConnection,
    save,
    discardChanges,
  } = controller;
  const managedRoute = routes.routes.find((route) => route.id === "managed");
  const managedStt = useManagedSttAvailability(
    managedRoute !== undefined &&
      managedRoute.reason_code !== "MANAGED_SERVICE_NOT_CONFIGURED",
    routes.reload,
  );
  const [discardConfirmationOpen, setDiscardConfirmationOpen] = useState(false);

  const requestClose = () => {
    if (!loaded || loadingError || !dirty) {
      onClose();
      return;
    }
    setDiscardConfirmationOpen(true);
  };

  const discardAndClose = () => {
    discardChanges();
    setDiscardConfirmationOpen(false);
    onClose();
  };

  const currentSectionError =
    sectionError?.category === activeCategory ? sectionError.message : null;
  const summaryMessage = loadingError ?? sectionError?.message ?? null;
  return (
    <>
      <Dialog
        open={!discardConfirmationOpen}
        onOpenChange={(open) => {
          if (!open) requestClose();
        }}
      >
        <DialogContent
          data-testid="settings-modal"
          title="設定"
          description="会議支援を自分の環境に合わせます"
          closeLabel="設定を閉じる"
          initialFocus="title"
          bodyClassName="flex min-h-0 flex-1 flex-col overflow-hidden"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            if (!discardConfirmationOpen && restoreFocusTo?.isConnected)
              restoreFocusTo.focus();
          }}
          className="h-[calc(100vh_-_1rem)] max-h-[760px] max-w-5xl rounded-2xl bg-paper md:h-[min(760px,92vh)]"
        >
          <div className="flex min-h-0 flex-1 flex-col md:flex-row">
            <SettingsNavigation
              active={activeCategory}
              onChange={(category) => {
                setActiveCategory(category);
                clearSaveMessage();
              }}
            />
            <main className="min-h-0 flex-1 overflow-y-auto px-4 py-5 md:px-7 md:py-6">
              {currentSectionError && (
                <div
                  className="mx-auto mb-4 flex w-full max-w-3xl items-start gap-2 rounded-xl border border-danger/20 bg-danger-soft p-3 text-xs font-medium text-danger"
                  role="alert"
                >
                  <CircleAlert
                    className="mt-0.5 h-4 w-4 shrink-0"
                    aria-hidden="true"
                  />
                  {currentSectionError}
                </div>
              )}
              {activeCategory !== "about" &&
              activeCategory !== "account" &&
              !loaded &&
              !loadingError ? (
                <div
                  className="flex h-48 items-center justify-center text-sm text-ink-muted"
                  role="status"
                >
                  設定を読み込んでいます
                </div>
              ) : activeCategory === "account" ? (
                <AccountSettingsPanel
                  offered={managedStt.offered}
                  onChanged={() => {
                    void managedStt.refresh();
                    void routes.reload();
                  }}
                />
              ) : activeCategory === "support" ? (
                <SupportMethodPanel
                  routes={routes.routes}
                  assignments={routes.draftAssignments}
                  loading={routes.loading}
                  manualReloadStatus={routes.manualReloadStatus}
                  error={routes.error ?? undefined}
                  credentialError={fieldErrors.support}
                  replyEnabled={form.replyFeatureEnabled}
                  replyAutoGenerate={form.replyAutoGenerate}
                  connectionStates={connectionStates}
                  secretsStatus={form.secretsStatus}
                  secretInputs={form.secretInputs}
                  connectionEditingProvider={connectionEditingProvider}
                  connectionTestingProvider={connectionTestingProvider}
                  connectionTestMessages={connectionTestMessages}
                  onBeginConnectionEdit={beginConnectionEdit}
                  onCancelConnectionEdit={cancelConnectionEdit}
                  onSecretChange={updateSecret}
                  onTestConnection={(provider) => {
                    void testConnection(provider);
                  }}
                  onRequestSecretDelete={scheduleSecretDeletion}
                  onCancelSecretDelete={cancelSecretDeletion}
                  onAssignmentChange={assignRoute}
                  onReplyEnabledChange={(enabled) =>
                    updateForm("replyFeatureEnabled", enabled)
                  }
                  onReplyAutoGenerateChange={(enabled) =>
                    updateForm("replyAutoGenerate", enabled)
                  }
                  onRouteAction={(route) => {
                    void handleRouteAction(route);
                  }}
                  onReload={() => {
                    void routes.reload();
                  }}
                />
              ) : activeCategory === "audio" ? (
                <AudioSettingsPanel
                  form={form}
                  errors={fieldErrors}
                  speechModel={speechModel}
                  speechModelActionsDisabled={busy}
                  connectionStates={connectionStates}
                  secretsStatus={form.secretsStatus}
                  secretInputs={form.secretInputs}
                  connectionEditingProvider={connectionEditingProvider}
                  managedStt={managedStt}
                  onManageAccount={() => setActiveCategory("account")}
                  connectionTestingProvider={connectionTestingProvider}
                  connectionTestMessages={connectionTestMessages}
                  onBeginConnectionEdit={beginConnectionEdit}
                  onCancelConnectionEdit={cancelConnectionEdit}
                  onSecretChange={updateSecret}
                  onTestConnection={(provider) => {
                    void testConnection(provider);
                  }}
                  onRequestSecretDelete={scheduleSecretDeletion}
                  onCancelSecretDelete={cancelSecretDeletion}
                  update={updateForm}
                />
              ) : activeCategory === "privacy" ? (
                <PrivacySettingsPanel
                  form={form}
                  selectedRoute={selectedRoute}
                  errors={fieldErrors}
                  update={updateForm}
                  onChooseContextDirectory={() => {
                    void chooseContextDirectory();
                  }}
                />
              ) : activeCategory === "advanced" ? (
                <AdvancedSettingsPanel
                  error={fieldErrors.advanced}
                  form={form}
                  acpRoute={routes.routes.find((route) => route.id === "acp")}
                  ollamaTesting={ollamaTesting}
                  ollamaMessage={ollamaMessage}
                  ollamaMessageIsError={ollamaMessageIsError}
                  update={updateForm}
                  onTestOllama={() => {
                    void testOllamaConnection();
                  }}
                />
              ) : (
                <AboutSettingsPanel />
              )}
            </main>
          </div>

          <footer className="sticky bottom-0 z-10 flex shrink-0 items-center gap-3 border-t border-line bg-surface px-5 py-3.5">
            <div className="min-w-0 flex-1">
              {summaryMessage ? (
                <button
                  type="button"
                  onClick={() =>
                    sectionError && setActiveCategory(sectionError.category)
                  }
                  className="flex max-w-full items-start gap-1.5 text-left text-xs font-medium text-danger hover:text-danger/80"
                >
                  <CircleAlert
                    className="mt-px h-3.5 w-3.5 shrink-0"
                    aria-hidden="true"
                  />
                  <span className="line-clamp-2">
                    {summaryMessage}
                    {sectionError
                      ? `（${CATEGORY_LABELS[sectionError.category]}）`
                      : ""}
                  </span>
                </button>
              ) : speechModel.blocksSettingsSave ? (
                <p className="text-xs font-semibold text-warning" role="status">
                  {speechModel.checkingStatus
                    ? "音声認識データの準備状況を確認してから設定を保存してください"
                    : "音声認識データの取得中は設定を保存できません"}
                </p>
              ) : saveMessage ? (
                <p
                  className="text-xs font-semibold text-positive"
                  role="status"
                >
                  {saveMessage}
                </p>
              ) : (
                <p className="hidden text-xs text-ink-muted md:block">
                  変更は「保存」を押すまで反映されません
                </p>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button variant="quiet" size="sm" onClick={requestClose}>
                閉じる
              </Button>
              {activeCategory !== "about" && (
                <Button
                  variant="primary"
                  size="sm"
                  loading={busy}
                  onClick={() => {
                    void save();
                  }}
                  disabled={
                    !loaded || routes.loading || speechModel.blocksSettingsSave
                  }
                >
                  保存
                </Button>
              )}
            </div>
          </footer>
        </DialogContent>
      </Dialog>
      <Dialog
        open={discardConfirmationOpen}
        onOpenChange={(open) => {
          if (!open) setDiscardConfirmationOpen(false);
        }}
      >
        <DialogContent
          title="変更を破棄しますか？"
          description="保存していない変更を破棄して、設定を閉じます。"
          closeLabel="破棄の確認を閉じる"
          bodyClassName="flex-none overflow-visible"
          className="max-w-md"
        >
          <div className="space-y-4 p-5">
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <Button
                variant="quiet"
                size="sm"
                onClick={() => setDiscardConfirmationOpen(false)}
              >
                設定に戻る
              </Button>
              <Button variant="danger" size="sm" onClick={discardAndClose}>
                変更を破棄して閉じる
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
