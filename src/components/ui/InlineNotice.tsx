import {
  CircleAlert,
  CircleCheck,
  Info,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "./cn";

export type NoticeTone = "info" | "positive" | "warning" | "danger";

const toneClasses: Record<NoticeTone, string> = {
  info: "border-primary/20 bg-primary-soft/70 text-primary",
  positive: "border-positive/20 bg-positive-soft text-positive",
  warning: "border-warning/20 bg-warning-soft text-warning",
  danger: "border-danger/20 bg-danger-soft text-danger",
};

const toneIcons: Record<NoticeTone, LucideIcon> = {
  info: Info,
  positive: CircleCheck,
  warning: TriangleAlert,
  danger: CircleAlert,
};

export interface InlineNoticeProps extends HTMLAttributes<HTMLDivElement> {
  tone?: NoticeTone;
  title?: string;
  children: ReactNode;
  action?: ReactNode;
}

export function InlineNotice({
  tone = "info",
  title,
  children,
  action,
  className,
  ...props
}: InlineNoticeProps) {
  const Icon = toneIcons[tone];

  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={cn(
        "flex min-w-0 items-start gap-3 rounded-[10px] border px-3.5 py-3 text-sm",
        toneClasses[tone],
        className,
      )}
      {...props}
    >
      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        {title && <p className="font-semibold text-current">{title}</p>}
        <div className={cn("leading-relaxed", title && "mt-0.5")}>
          {children}
        </div>
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
