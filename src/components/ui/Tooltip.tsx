import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import {
  createContext,
  useContext,
  type ReactElement,
  type ReactNode,
} from "react";

export const TooltipProvider = TooltipPrimitive.Provider;
const TooltipPortalContext = createContext<HTMLElement | null>(null);

export interface TooltipPortalProviderProps {
  container: HTMLElement | null;
  children: ReactNode;
}

export function TooltipPortalProvider({
  container,
  children,
}: TooltipPortalProviderProps) {
  return (
    <TooltipPortalContext.Provider value={container}>
      {children}
    </TooltipPortalContext.Provider>
  );
}

export interface TooltipProps {
  content: ReactNode;
  children: ReactElement;
  side?: "top" | "right" | "bottom" | "left";
  sideOffset?: number;
}

export function Tooltip({
  content,
  children,
  side = "bottom",
  sideOffset = 7,
}: TooltipProps) {
  const portalContainer = useContext(TooltipPortalContext);
  return (
    <TooltipPrimitive.Provider delayDuration={350}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal container={portalContainer ?? undefined}>
          <TooltipPrimitive.Content
            side={side}
            collisionBoundary={portalContainer ?? undefined}
            collisionPadding={8}
            sideOffset={sideOffset}
            className="z-[70] max-w-64 rounded-lg border border-ink/10 bg-ink px-2.5 py-1.5 text-xs leading-relaxed text-surface shadow-lg data-[state=delayed-open]:animate-fade-in"
          >
            {content}
            <TooltipPrimitive.Arrow className="fill-ink" />
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}
