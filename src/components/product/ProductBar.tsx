import { History, Home, Settings } from "lucide-react";
import { Button } from "../ui/Button";
import { Tooltip } from "../ui/Tooltip";
import { cn } from "../ui/cn";
import { useAlwaysOnTop } from "../../hooks/useAlwaysOnTop";
import { AlwaysOnTopControl } from "../window/AlwaysOnTopControl";

export type ProductDestination = "home" | "reflection";

interface ProductBarProps {
  active: ProductDestination;
  onNavigate: (destination: ProductDestination) => void;
  onSettings: () => void;
  status: string;
}

export function ProductBar({
  active,
  onNavigate,
  onSettings,
  status,
}: ProductBarProps) {
  const alwaysOnTop = useAlwaysOnTop({ defaultDesired: false });

  return (
    <nav
      aria-label="アプリツールバー"
      className="product-bar flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-4"
    >
      <div className="product-bar__brand flex min-w-0 items-center gap-2 border-r border-line pr-3">
        <span className="product-bar__brand-name shrink-0 font-display text-[15px] font-bold tracking-[0.03em] text-ink">
          会議サポート
        </span>
        <span
          className="product-bar__status rounded-full bg-paper px-2 py-1 text-xs font-semibold text-ink-muted"
          role="status"
        >
          {status}
        </span>
      </div>

      <div className="flex min-w-0 flex-1 items-center gap-1">
        <Button
          variant="quiet"
          size="sm"
          aria-current={active === "home" ? "page" : undefined}
          onClick={() => onNavigate("home")}
          className={cn(
            active === "home" &&
              "bg-primary-soft text-primary hover:bg-primary-soft",
          )}
        >
          <Home aria-hidden="true" className="size-4" />
          <span className="product-bar__nav-label">ホーム</span>
        </Button>
        <Button
          variant="quiet"
          size="sm"
          aria-current={active === "reflection" ? "page" : undefined}
          onClick={() => onNavigate("reflection")}
          className={cn(
            active === "reflection" &&
              "bg-primary-soft text-primary hover:bg-primary-soft",
          )}
        >
          <History aria-hidden="true" className="size-4" />
          <span className="product-bar__nav-label">履歴</span>
        </Button>
      </div>

      <Tooltip content="設定">
        <Button
          variant="quiet"
          size="icon"
          onClick={onSettings}
          aria-label="設定"
        >
          <Settings aria-hidden="true" className="size-4" />
        </Button>
      </Tooltip>
      <AlwaysOnTopControl controller={alwaysOnTop} />
    </nav>
  );
}
