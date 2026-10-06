import { useState } from "react";
import { useMeetingStore } from "../store/meetingStore";
import type { SendFn, SocketState } from "../types";
import { AppFrame } from "./product/AppFrame";
import { SetupScreen } from "./SetupScreen";
import { TooltipProvider } from "./ui/Tooltip";

function createPreviewState(): SocketState {
  return {
    ...useMeetingStore.getState(),
    connected: true,
    sttInitialized: true,
    devices: [
      { index: 1, name: "会議アプリの音声", is_monitor: true },
      { index: 2, name: "MacBookのマイク", is_monitor: false },
    ],
    deviceOther: 1,
    deviceSelf: 2,
    levelOther: 0.16,
    levelSelf: 0.08,
  };
}

export function SetupScreenPreview() {
  const [state] = useState<SocketState>(createPreviewState);

  const send: SendFn = () => undefined;

  return (
    <TooltipProvider>
      <div className="flex h-screen flex-col overflow-hidden bg-paper">
        <AppFrame
          active="home"
          connected
          onNavigate={() => undefined}
          onSettings={() => undefined}
        >
          <SetupScreen
            state={state}
            send={send}
            showFirstRunGuidance={false}
            onSettings={() => undefined}
            replyStatus={{
              readiness: "ready",
              canGenerate: true,
              message: null,
            }}
            replyReloadStatus="idle"
            onReloadReplyStatus={() => undefined}
            speechReadiness="ready"
          />
        </AppFrame>
      </div>
    </TooltipProvider>
  );
}
