import {
  Suspense,
  lazy,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Check, X } from "lucide-react";
import { client } from "./api/generated/client.gen";
import { BootstrapScreen } from "./components/BootstrapScreen";
import { AppFrame } from "./components/product/AppFrame";
import type { ProductDestination } from "./components/product/ProductBar";
import { SetupScreen } from "./components/SetupScreen";
import { Button } from "./components/ui/Button";
import { InlineNotice } from "./components/ui/InlineNotice";
import { TooltipProvider } from "./components/ui/Tooltip";
import { useBackendBootstrapStatus } from "./hooks/useBackendBootstrapStatus";
import { useMeetingSocket } from "./hooks/useMeetingSocket";
import { useAiRoutes } from "./hooks/useAiRoutes";
import {
  isAssistantPanelPreviewEnabled,
  isMeetingWorkspacePreviewEnabled,
} from "./platform/previewMode";
import {
  getCurrentAppWindowLabel,
  setAssistantWindowVisible,
} from "./platform/tauriWindow";
import { useMeetingStore } from "./store/meetingStore";
import type { SendFn, SocketState } from "./types";

const FIRST_MEETING_STARTED_KEY = "meeting-supporter.first-meeting-started";

function shouldShowFirstRunGuidance(): boolean {
  try {
    return window.localStorage.getItem(FIRST_MEETING_STARTED_KEY) !== "true";
  } catch {
    return true;
  }
}

function rememberFirstMeetingStarted(): void {
  try {
    window.localStorage.setItem(FIRST_MEETING_STARTED_KEY, "true");
  } catch {
    // Persistence is optional; a storage failure must not block a meeting.
  }
}

const AssistantSidePanelPreview = lazy(() =>
  import("./components/assistant/LiveReplySidePanelPreview").then((module) => ({
    default: module.LiveReplySidePanelPreview,
  })),
);
const MeetingWorkspacePreview = lazy(() =>
  import("./components/MainMeetingControlScreenPreview").then((module) => ({
    default: module.MainMeetingControlScreenPreview,
  })),
);
const LiveReplySidePanel = lazy(() =>
  import("./components/assistant/LiveReplySidePanel").then((module) => ({
    default: module.LiveReplySidePanel,
  })),
);

const MeetingHistoryScreen = lazy(() =>
  import("./components/history/MeetingHistoryScreen").then((module) => ({
    default: module.MeetingHistoryScreen,
  })),
);

const MainMeetingControlScreen = lazy(() =>
  import("./components/MainMeetingControlScreen").then((module) => ({
    default: module.MainMeetingControlScreen,
  })),
);

const SettingsModal = lazy(() =>
  import("./components/SettingsModal").then((module) => ({
    default: module.SettingsModal,
  })),
);

function ScreenLoadingState({
  message = "画面を準備しています…",
}: {
  message?: string;
}) {
  return (
    <div
      role="status"
      className="flex h-full min-h-32 items-center justify-center bg-paper text-xs text-ink-muted"
    >
      {message}
    </div>
  );
}

interface ConfiguredClientBoundaryProps {
  apiPort: number | null;
  apiAuthToken: string | null;
  fallback: ReactNode;
  children: ReactNode;
}

function ConfiguredClientBoundary({
  apiPort,
  apiAuthToken,
  fallback,
  children,
}: ConfiguredClientBoundaryProps) {
  const [configuredFor, setConfiguredFor] = useState<{
    apiPort: number;
    apiAuthToken: string;
  } | null>(null);

  useEffect(() => {
    if (!apiPort || !apiAuthToken) {
      setConfiguredFor(null);
      return;
    }

    client.setConfig({
      baseUrl: `http://127.0.0.1:${apiPort}`,
      headers: { Authorization: `Bearer ${apiAuthToken}` },
    });
    setConfiguredFor({ apiPort, apiAuthToken });
  }, [apiPort, apiAuthToken]);

  const configured =
    configuredFor !== null &&
    configuredFor.apiPort === apiPort &&
    configuredFor.apiAuthToken === apiAuthToken;
  return configured ? children : fallback;
}

export default function App() {
  if (
    import.meta.env.DEV &&
    isAssistantPanelPreviewEnabled(window.location.search, import.meta.env.DEV)
  ) {
    return (
      <Suspense fallback={<div className="min-h-screen bg-paper text-ink" />}>
        <AssistantSidePanelPreview />
      </Suspense>
    );
  }

  if (
    import.meta.env.DEV &&
    isMeetingWorkspacePreviewEnabled(
      window.location.search,
      import.meta.env.DEV,
    )
  ) {
    return (
      <Suspense fallback={<div className="min-h-screen bg-paper text-ink" />}>
        <MeetingWorkspacePreview />
      </Suspense>
    );
  }

  if (getCurrentAppWindowLabel() === "assistant") return <AssistantWindowApp />;
  return <MainWindowApp />;
}

function AssistantWindowApp() {
  const { apiPort, apiAuthToken } = useBackendBootstrapStatus();

  const { send } = useMeetingSocket(apiPort, apiAuthToken);
  const state = useMeetingStore();

  return (
    <ConfiguredClientBoundary
      apiPort={apiPort}
      apiAuthToken={apiAuthToken}
      fallback={
        <div
          role="status"
          className="flex h-screen items-center justify-center bg-paper text-xs text-ink-muted"
        >
          準備しています…
        </div>
      }
    >
      <Suspense
        fallback={<ScreenLoadingState message="ライブ支援を準備しています…" />}
      >
        <LiveReplySidePanel
          state={state}
          send={send}
          onDiscardReply={state.discardActiveReply}
        />
      </Suspense>
    </ConfiguredClientBoundary>
  );
}

interface MainWindowContentProps {
  state: SocketState;
  send: SendFn;
  screen: ProductDestination;
  settingsOpen: boolean;
  showFirstRunGuidance: boolean;
  onNavigate: (destination: ProductDestination) => void;
  settingsReturnFocusTo: HTMLElement | null;
  onOpenSettings: () => void;
  onCloseSettings: () => void;
}

function MainWindowContent({
  state,
  send,
  screen,
  settingsOpen,
  showFirstRunGuidance,
  onNavigate,
  onOpenSettings,
  onCloseSettings,
  settingsReturnFocusTo,
}: MainWindowContentProps) {
  const routes = useAiRoutes();

  return (
    <>
      <AppFrame
        active={screen}
        onNavigate={onNavigate}
        onSettings={onOpenSettings}
        status={
          !state.connected
            ? "接続確認中"
            : state.isRunning
              ? "会議中"
              : "待機中"
        }
        connectionNotice={
          !state.connected ? (
            <div className="shrink-0 px-4 pt-3">
              <InlineNotice tone="warning" title="接続を戻しています">
                画面はそのままにしてお待ちください。操作は接続後に再開できます。
              </InlineNotice>
            </div>
          ) : undefined
        }
      >
        <Suspense fallback={<ScreenLoadingState />}>
          {state.isRunning ? (
            <MainMeetingControlScreen
              key="meeting-control"
              state={state}
              send={send}
              onSettings={onOpenSettings}
              replyReadiness={routes.replyStatus.readiness}
              infoRouteStatus={routes.infoRouteStatus}
            />
          ) : screen === "reflection" ? (
            <MeetingHistoryScreen
              key="history"
              onBack={() => onNavigate("home")}
              minutesRouteStatus={routes.minutesRouteStatus}
              onSettings={onOpenSettings}
            />
          ) : (
            <SetupScreen
              key="setup"
              state={state}
              send={send}
              showFirstRunGuidance={showFirstRunGuidance}
              onSettings={onOpenSettings}
              onHistory={() => onNavigate("reflection")}
              replyStatus={routes.replyStatus}
              replyReloadStatus={routes.manualReloadStatus}
              onReloadReplyStatus={() => {
                void routes.reload();
              }}
            />
          )}
        </Suspense>
      </AppFrame>

      {settingsOpen && (
        <div className="absolute inset-0 z-30">
          <Suspense
            fallback={<ScreenLoadingState message="設定を準備しています…" />}
          >
            <SettingsModal
              onClose={() => {
                routes.resetDraftAssignments();
                onCloseSettings();
              }}
              routes={routes}
              audioSettingsLocked={state.isRunning}
              restoreFocusTo={settingsReturnFocusTo}
            />
          </Suspense>
        </div>
      )}
    </>
  );
}

function MainWindowApp() {
  const settingsReturnFocusRef = useRef<HTMLElement | null>(null);
  const { apiPort, apiAuthToken, bootstrap, crashInfo } =
    useBackendBootstrapStatus();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const openSettings = () => {
    settingsReturnFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setSettingsOpen(true);
  };
  const [screen, setScreen] = useState<ProductDestination>("home");
  const [savedToastVisible, setSavedToastVisible] = useState(false);
  const [showFirstRunGuidance, setShowFirstRunGuidance] = useState(
    shouldShowFirstRunGuidance,
  );
  const prevRunningRef = useRef(false);
  const backendReady = Boolean(apiPort && apiAuthToken);

  const { send } = useMeetingSocket(apiPort, apiAuthToken);
  const state = useMeetingStore();

  useEffect(() => {
    if (!state.isRunning) void setAssistantWindowVisible(false);
  }, [state.isRunning]);

  useEffect(() => {
    if (state.isRunning && screen === "reflection") setScreen("home");
  }, [state.isRunning, screen]);

  useEffect(() => {
    if (prevRunningRef.current && !state.isRunning && state.connected) {
      setSavedToastVisible(true);
    } else if (!prevRunningRef.current && state.isRunning) {
      setSavedToastVisible(false);
    }
    prevRunningRef.current = state.isRunning;
  }, [state.isRunning, state.connected]);

  useEffect(() => {
    if (!state.isRunning || !showFirstRunGuidance) return;
    rememberFirstMeetingStarted();
    setShowFirstRunGuidance(false);
  }, [showFirstRunGuidance, state.isRunning]);

  return (
    <TooltipProvider delayDuration={350}>
      <div className="relative flex h-screen min-w-0 flex-col overflow-hidden bg-paper text-ink">
        {!backendReady ? (
          <BootstrapScreen
            phase={bootstrap.phase}
            message={bootstrap.message}
            crashInfo={crashInfo}
          />
        ) : (
          <ConfiguredClientBoundary
            apiPort={apiPort}
            apiAuthToken={apiAuthToken}
            fallback={
              <BootstrapScreen
                phase={bootstrap.phase}
                message={bootstrap.message}
                crashInfo={crashInfo}
              />
            }
          >
            <MainWindowContent
              state={state}
              send={send}
              screen={screen}
              showFirstRunGuidance={showFirstRunGuidance}
              settingsOpen={settingsOpen}
              onNavigate={setScreen}
              settingsReturnFocusTo={settingsReturnFocusRef.current}
              onOpenSettings={openSettings}
              onCloseSettings={() => setSettingsOpen(false)}
            />
          </ConfiguredClientBoundary>
        )}

        {savedToastVisible && (
          <div className="absolute bottom-4 right-4 z-40 w-[calc(100vw_-_2rem)] max-w-[380px] animate-slide-up">
            <InlineNotice
              tone="positive"
              title="会議を保存しました"
              action={
                <Button
                  variant="quiet"
                  size="icon"
                  onClick={() => setSavedToastVisible(false)}
                  aria-label="通知を閉じる"
                >
                  <X aria-hidden="true" className="size-4" />
                </Button>
              }
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1.5">
                  <Check aria-hidden="true" className="size-3.5" />
                  ふりかえりから確認できます。
                </span>
                <Button
                  variant="quiet"
                  size="sm"
                  onClick={() => {
                    setScreen("reflection");
                    setSavedToastVisible(false);
                  }}
                  className="text-positive hover:bg-positive-soft"
                >
                  開く
                </Button>
              </div>
            </InlineNotice>
          </div>
        )}
      </div>
    </TooltipProvider>
  );
}
