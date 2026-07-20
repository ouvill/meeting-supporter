import { LoaderCircle } from "lucide-react";
import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "./cn";

export type StatusTone = "neutral" | "positive" | "warning" | "danger" | "busy";

const toneClasses: Record<StatusTone, string> = {
  neutral: "border-line bg-surface-muted text-ink-muted",
  positive: "border-positive/20 bg-positive-soft text-positive",
  warning: "border-warning/20 bg-warning-soft text-warning",
  danger: "border-danger/20 bg-danger-soft text-danger",
  busy: "border-primary/20 bg-primary-soft text-primary",
};

const dotClasses: Record<StatusTone, string> = {
  neutral: "bg-ink-faint",
  positive: "bg-positive",
  warning: "bg-warning",
  danger: "bg-danger",
  busy: "bg-primary",
};

export interface StatusProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: StatusTone;
  children: ReactNode;
}

export function Status({
  tone = "neutral",
  children,
  className,
  ...props
}: StatusProps) {
  return (
    <span
      className={cn(
        "inline-flex min-h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-semibold",
        toneClasses[tone],
        className,
      )}
      {...props}
    >
      {tone === "busy" ? (
        <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" />
      ) : (
        <span
          aria-hidden="true"
          className={cn("size-1.5 rounded-full", dotClasses[tone])}
        />
      )}
      {children}
    </span>
  );
}
