import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "./cn";

export interface SurfaceProps extends HTMLAttributes<HTMLElement> {
  title?: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
}

export function Surface({
  title,
  description,
  action,
  children,
  className,
  ...props
}: SurfaceProps) {
  return (
    <section
      className={cn(
        "min-w-0 rounded-xl border border-line bg-surface shadow-card",
        className,
      )}
      {...props}
    >
      {(title || description || action) && (
        <header className="flex min-w-0 items-start justify-between gap-4 border-b border-line px-4 py-3.5">
          <div className="min-w-0">
            {title && (
              <h2 className="font-display text-[15px] font-bold tracking-[0.01em] text-ink">
                {title}
              </h2>
            )}
            {description && (
              <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">
                {description}
              </p>
            )}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </header>
      )}
      {children}
    </section>
  );
}
