import { useEffect, useId, useRef, useState } from "react";
import { CircleAlert, X } from "lucide-react";
import type { AiRoutesController } from "../hooks/useAiRoutes";
import type { SendFn, SocketState } from "../types";
import { AudioInputs } from "./setup/AudioInputs";
import { AgentModelControl } from "./settings/AgentModelControl";
import { Dialog, DialogContent } from "./ui/Dialog";
import { Button } from "./ui/Button";
import { AiModelSettings } from "./settings/AiModelSettings";
import { AboutSettingsPanel } from "./settings/AboutSettingsPanel";
import { AudioSettingsPanel } from "./settings/AudioSettingsPanel";
import { PrivacySettingsPanel } from "./settings/PrivacySettingsPanel";
import {
  SettingsNavigation,
  SettingsPage,
  type SettingsView,
} from "./settings/SettingsPrimitives";
import { Tooltip } from "./ui/Tooltip";
import type { ConnectionProvider } from "./settings/ApiConnectionControl";
import { SupportMethodPanel } from "./settings/SupportMethodPanel";
import type { SettingsCategory } from "./settings/types";
import { useSettingsForm } from "./settings/useSettingsForm";

interface Props {
  onClose: () => void;
  routes: AiRoutesController;
  restoreFocusTo?: HTMLElement | null;
  audioSettingsLocked?: boolean;
  state?: SocketState;
  send?: SendFn;
  initialSection?: "reply" | "speech";
  /** Changes when something outside asks to leave settings, e.g. the sidebar. */
  closeRequestToken?: number;
  /** The user chose to stay after a close request found unsaved changes. */
  onCloseCancelled?: () => void;
}

type AiSettingsTab = "reply" | "speech";

const CATEGORY_LABELS: Record<SettingsCategory, string> = {
  support: "返答案・文字起こし",
  audio: "マイクと音声",
  privacy: "データと保存",
  about: "このアプリについて",
};

export function SettingsModal({
  onClose,
  routes,
  restoreFocusTo,
  audioSettingsLocked = false,
  state,
  send,
  initialSection = "reply",
  closeRequestToken,
  onCloseCancelled,
}: Props) {
  const titleId = useId();
  const titleRef = useRef<HTMLHeadingElement>(null);
  const restoreFocusRef = useRef(restoreFocusTo);
  restoreFocusRef.current = restoreFocusTo;
  useEffect(() => {
    titleRef.current?.focus();
    return () => {
      // Leaving through another control keeps the focus where the user put it.
      const target = restoreFocusRef.current;
      if (document.activeElement === document.body && target?.isConnected)
        target.focus();
    };
  }, []);
  const controller = useSettingsForm({ routes, audioSettingsLocked });
  const {
    form,
    agentModels,
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
    save,
    discardChanges,
  } = controller;
  const [discardConfirmationOpen, setDiscardConfirmationOpen] = useState(false);
  const lockedConnectionProviders = new Set<ConnectionProvider>();
  const speechModelBlocksSave =
    speechModel.blocksSettingsSave && !audioSettingsLocked;

  const [activeTab, setActiveTab] = useState<AiSettingsTab>(initialSection);
  const [connectionRouteId, setConnectionRouteId] = useState<string | null>(
    null,
  );
  useEffect(() => {
    setActiveTab(initialSection);
  }, [initialSection]);
  const errorTab: AiSettingsTab =
    fieldErrors.support || fieldErrors.advanced || agentModels.error
      ? "reply"
      : fieldErrors.audio
        ? "speech"
        : "reply";
  useEffect(() => {
    if (sectionError?.category === "support") setActiveTab(errorTab);
  }, [sectionError, errorTab]);

  const requestClose = () => {
    if (!loaded || loadingError || !dirty) {
      onClose();
      return;
    }
    setDiscardConfirmationOpen(true);
  };

  const requestCloseRef = useRef(requestClose);
  requestCloseRef.current = requestClose;
  const handledCloseRequestRef = useRef(closeRequestToken);
  useEffect(() => {
    if (handledCloseRequestRef.current === closeRequestToken) return;
    handledCloseRequestRef.current = closeRequestToken;
    requestCloseRef.current();
  }, [closeRequestToken]);

  const view: SettingsView =
    activeCategory === "support" ? activeTab : activeCategory;
  const selectView = (next: SettingsView) => {
    if (next === "reply" || next === "speech") {
      setActiveCategory("support");
      setActiveTab(next);
    } else {
      setActiveCategory(next);
    }
    clearSaveMessage();
  };

  const discardAndClose = () => {
    discardChanges();
    setDiscardConfirmationOpen(false);
    onClose();
  };

  const currentSectionError =
    sectionError?.category === activeCategory ? sectionError.message : null;
  const summaryMessage = loadingError ?? sectionError?.message ?? null;
  // Nothing to save, nothing to show: the bar appears with the first change.
  const saveBarVisible =
    dirty || busy || summaryMessage !== null || saveMessage !== null;
  return (
    <>
      <section
        data-testid="settings-modal"
        aria-labelledby={titleId}
        className="absolute inset-0 z-20 flex min-h-0 flex-col bg-surface text-ink"
        onKeyDown={(event) => {
          if (event.key !== "Escape" || event.defaultPrevented) return;
          event.preventDefault();
          requestClose();
        }}
      >
        <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-line pl-6 pr-4 md:pl-9">
          <h2
            id={titleId}
            ref={titleRef}
            tabIndex={-1}
            className="font-display text-lg font-bold text-ink outline-none focus-visible:shadow-none!"
          >
            設定
          </h2>
          <Tooltip content="設定を閉じる">
            <Button
              variant="quiet"
              size="icon"
              aria-label="設定を閉じる"
              onClick={requestClose}
            >
              <X aria-hidden="true" className="size-4" />
            </Button>
          </Tooltip>
        </header>
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <SettingsNavigation active={view} onChange={selectView} />
          <main className="min-h-0 flex-1 overflow-y-auto px-6 py-6 md:px-9 md:py-8">
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
            {activeCategory !== "about" && !loaded && !loadingError ? (
              <div
                className="flex h-48 items-center justify-center text-sm text-ink-muted"
                role="status"
              >
                設定を読み込んでいます
              </div>
            ) : activeCategory === "support" ? (
              <>
                <div
                  id="ai-settings-panel-reply"
                  hidden={activeTab !== "reply"}
                >
                  <SettingsPage
                    title="返答案"
                    description="会議中、相手の発言への返答の案をAIが作ります。使うAIをここで選びます。"
                  >
                    <SupportMethodPanel
                      connectionRouteId={connectionRouteId}
                      onConnectionRouteChange={setConnectionRouteId}
                      agentsLocked={audioSettingsLocked || busy}
                      routes={routes.routes}
                      lockedConnectionProviders={lockedConnectionProviders}
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
                      onReload={() => {
                        void routes.reload();
                      }}
                      modelSettings={
                        selectedRoute?.id.startsWith("acp:") ? (
                          <AgentModelControl
                            routeId={selectedRoute.id}
                            locked={audioSettingsLocked || busy}
                            settings={agentModels}
                          />
                        ) : selectedRoute &&
                          ["openai", "gemini", "anthropic", "ollama"].includes(
                            selectedRoute.id,
                          ) ? (
                          <AiModelSettings
                            provider={selectedRoute.id}
                            error={fieldErrors.advanced}
                            form={form}
                            update={updateForm}
                          />
                        ) : null
                      }
                    />
                  </SettingsPage>
                </div>
                <div
                  id="ai-settings-panel-speech"
                  hidden={activeTab !== "speech"}
                >
                  <SettingsPage
                    title="文字起こし"
                    description="会議の音声を文字にします。音声はこの端末の中で処理し、外部へ送りません。"
                  >
                    <AudioSettingsPanel
                      form={form}
                      errors={fieldErrors}
                      speechModel={speechModel}
                      speechModelActionsDisabled={busy}
                      audioSettingsLocked={audioSettingsLocked}
                      update={updateForm}
                    />
                  </SettingsPage>
                </div>
              </>
            ) : activeCategory === "audio" ? (
              <SettingsPage
                title="マイクと音声"
                description="会議で使うマイクと、相手の声を拾う音声を選びます。話すか音を流して音量バーが動けば準備完了です。変更はすぐに反映されます。"
              >
                {audioSettingsLocked && (
                  <p className="text-sm text-ink-muted">
                    会議中・準備中は音声入力を変更できません。
                  </p>
                )}
                {state && send ? (
                  <AudioInputs
                    state={state}
                    send={send}
                    locked={audioSettingsLocked}
                  />
                ) : (
                  <p className="text-sm text-ink-muted">
                    音声入力は会議前の画面で確認できます。
                  </p>
                )}
              </SettingsPage>
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
            ) : (
              <AboutSettingsPanel />
            )}
          </main>
        </div>

        {saveBarVisible && (
          <footer
            aria-label="設定の保存"
            className="flex shrink-0 animate-slide-up items-center gap-3 border-t border-line bg-surface px-6 py-3.5 shadow-sticky md:px-9"
          >
            <div className="min-w-0 flex-1">
              {summaryMessage ? (
                <button
                  type="button"
                  onClick={() => {
                    if (sectionError) setActiveCategory(sectionError.category);
                    setActiveTab(errorTab);
                  }}
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
              ) : speechModelBlocksSave ? (
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
                <p className="text-sm font-medium text-ink" role="status">
                  保存していない変更があります
                </p>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button variant="quiet" size="sm" onClick={requestClose}>
                閉じる
              </Button>
              {dirty && (
                <Button
                  variant="primary"
                  size="sm"
                  loading={busy}
                  onClick={() => {
                    void save();
                  }}
                  disabled={!loaded || routes.loading || speechModelBlocksSave}
                >
                  保存
                </Button>
              )}
            </div>
          </footer>
        )}
      </section>
      <Dialog
        open={discardConfirmationOpen}
        onOpenChange={(open) => {
          if (open) return;
          setDiscardConfirmationOpen(false);
          onCloseCancelled?.();
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
                onClick={() => {
                  setDiscardConfirmationOpen(false);
                  onCloseCancelled?.();
                }}
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
