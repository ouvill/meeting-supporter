import type { ReactNode } from "react";
import { AppSidebar, type ProductDestination } from "./AppSidebar";

interface AppFrameProps {
  active: ProductDestination;
  onNavigate: (destination: ProductDestination) => void;
  onSettings: () => void;
  connected: boolean;
  meetingActive?: boolean;
  connectionNotice?: ReactNode;
  /** A page shown over the current screen, which stays mounted beneath it. */
  settings?: ReactNode;
  children: ReactNode;
}

export function AppFrame({
  active,
  onNavigate,
  onSettings,
  connected,
  meetingActive,
  connectionNotice,
  settings,
  children,
}: AppFrameProps) {
  return (
    <div className="app-frame flex min-h-0 min-w-0 flex-1 overflow-hidden bg-paper">
      <AppSidebar
        active={active}
        onNavigate={onNavigate}
        onSettings={onSettings}
        connected={connected}
        meetingActive={meetingActive}
        settingsOpen={Boolean(settings)}
      />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-surface">
        {connectionNotice}
        <div className="app-frame__content relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <div
            className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
            inert={settings ? true : undefined}
          >
            {children}
          </div>
          {settings}
        </div>
      </div>
    </div>
  );
}
