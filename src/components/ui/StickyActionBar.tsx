import type { HTMLAttributes } from "react";
import { cn } from "./cn";

export function StickyActionBar({
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "sticky bottom-0 z-10 flex min-w-0 items-center justify-end gap-2 border-t border-line bg-surface px-4 py-3 shadow-sticky",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}
