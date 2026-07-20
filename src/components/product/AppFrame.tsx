import type { ReactNode } from "react";
import { ProductBar, type ProductDestination } from "./ProductBar";

interface AppFrameProps {
  active: ProductDestination;
  onNavigate: (destination: ProductDestination) => void;
  onSettings: () => void;
  status: string;
  connectionNotice?: ReactNode;
  children: ReactNode;
}

export function AppFrame({
  active,
  onNavigate,
  onSettings,
  status,
  connectionNotice,
  children,
}: AppFrameProps) {
  return (
    <div className="app-frame flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-paper">
      <ProductBar
        active={active}
        onNavigate={onNavigate}
        onSettings={onSettings}
        status={status}
      />
      {connectionNotice}
      <div className="app-frame__content flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {children}
      </div>
    </div>
  );
}
