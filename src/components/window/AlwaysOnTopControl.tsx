import { CircleAlert, LoaderCircle, Pin, PinOff } from "lucide-react";
import type { AlwaysOnTopController } from "../../hooks/useAlwaysOnTop";
import { Button } from "../ui/Button";
import { Tooltip } from "../ui/Tooltip";

interface AlwaysOnTopControlProps {
  controller: AlwaysOnTopController;
}

export function AlwaysOnTopControl({ controller }: AlwaysOnTopControlProps) {
  const { actual, busy, issue, statusMessage, retry, toggle } = controller;
  const known = actual !== "unknown";
  const enabled = actual === "on";
  const title = known
    ? `常に前面 ${enabled ? "ON" : "OFF"}`
    : "常に前面 未確認";
  const actionLabel = known
    ? enabled
      ? "常に前面表示を解除"
      : "常に前面表示を有効化"
    : "前面固定を再確認";

  return (
    <div className="flex min-w-0 items-center gap-1.5">
      {statusMessage && (
        <span
          className={`hidden max-w-52 truncate text-xs sm:inline ${issue ? "text-warning" : "text-ink-muted"}`}
          role="status"
          title={statusMessage}
        >
          {issue && (
            <CircleAlert aria-hidden="true" className="mr-1 inline size-3.5" />
          )}
          {statusMessage}
        </span>
      )}
      <Tooltip content={statusMessage ?? actionLabel}>
        <Button
          variant="quiet"
          size="icon"
          type="button"
          title={title}
          aria-label={actionLabel}
          aria-pressed={known ? enabled : undefined}
          aria-busy={busy || undefined}
          disabled={busy}
          onClick={() => void (known ? toggle() : retry())}
        >
          {busy ? (
            <LoaderCircle
              aria-hidden="true"
              className="size-4 animate-spin motion-reduce:animate-none"
            />
          ) : enabled ? (
            <Pin aria-hidden="true" className="size-4" />
          ) : (
            <PinOff aria-hidden="true" className="size-4" />
          )}
        </Button>
      </Tooltip>
    </div>
  );
}
