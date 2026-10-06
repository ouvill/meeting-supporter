import {
  AudioLines,
  History,
  Plus,
  Radio,
  Settings,
  type LucideIcon,
} from "lucide-react";
import { useAlwaysOnTop } from "../../hooks/useAlwaysOnTop";
import { cn } from "../ui/cn";
import { AlwaysOnTopControl } from "../window/AlwaysOnTopControl";

export type ProductDestination = "home" | "reflection";

interface AppSidebarProps {
  active: ProductDestination;
  onNavigate: (destination: ProductDestination) => void;
  onSettings: () => void;
  connected: boolean;
  meetingActive?: boolean;
  settingsOpen?: boolean;
}

export function AppSidebar({
  active,
  onNavigate,
  onSettings,
  connected,
  meetingActive = false,
  settingsOpen = false,
}: AppSidebarProps) {
  const alwaysOnTop = useAlwaysOnTop({ defaultDesired: false });
  const status = !connected
    ? "接続確認中"
    : meetingActive
      ? "会議中"
      : "待機中";

  return (
    <nav
      aria-label="メインナビゲーション"
      className="app-sidebar flex shrink-0 flex-col gap-1 border-r border-line bg-paper px-3 pb-3 pt-4"
    >
      <div className="app-sidebar__brand mb-4 flex h-8 items-center gap-2.5 px-1.5">
        <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary text-white">
          <AudioLines aria-hidden="true" className="size-4" />
        </span>
        <span className="app-sidebar__label truncate font-display text-[15px] font-bold tracking-[0.02em] text-ink">
          会議サポート
        </span>
      </div>

      {meetingActive ? (
        <SidebarItem
          icon={Radio}
          label="進行中の会議"
          current={!settingsOpen}
          onClick={() => onNavigate("home")}
        />
      ) : (
        <SidebarItem
          icon={Plus}
          label="新しい会議"
          current={!settingsOpen && active === "home"}
          onClick={() => onNavigate("home")}
        />
      )}
      <SidebarItem
        icon={History}
        label="履歴"
        current={!settingsOpen && !meetingActive && active === "reflection"}
        disabled={meetingActive}
        title={meetingActive ? "会議の終了後に確認できます" : undefined}
        onClick={() => onNavigate("reflection")}
      />

      <div className="mt-auto flex flex-col gap-1">
        <SidebarItem
          icon={Settings}
          label="設定"
          ariaLabel="設定"
          current={settingsOpen}
          onClick={onSettings}
        />
        <div className="app-sidebar__footer mt-1 flex min-w-0 items-center justify-between gap-1 border-t border-line pl-2.5 pt-2">
          <span
            role="status"
            className="flex min-w-0 items-center gap-2 text-xs font-medium text-ink-muted"
          >
            <span
              aria-hidden="true"
              className={cn(
                "size-2 shrink-0 rounded-full",
                !connected
                  ? "bg-warning"
                  : meetingActive
                    ? "bg-positive"
                    : "bg-line-strong",
              )}
            />
            <span className="app-sidebar__label truncate">{status}</span>
          </span>
          <AlwaysOnTopControl controller={alwaysOnTop} compact />
        </div>
      </div>
    </nav>
  );
}

interface SidebarItemProps {
  icon: LucideIcon;
  label: string;
  ariaLabel?: string;
  current?: boolean;
  disabled?: boolean;
  title?: string;
  onClick?: () => void;
}

function SidebarItem({
  icon: Icon,
  label,
  ariaLabel,
  current = false,
  disabled = false,
  title,
  onClick,
}: SidebarItemProps) {
  return (
    <button
      type="button"
      aria-label={ariaLabel}
      aria-current={current ? "page" : undefined}
      disabled={disabled}
      title={title ?? label}
      onClick={onClick}
      className={cn(
        "app-sidebar__item flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45 motion-reduce:transition-none",
        current
          ? "bg-surface text-ink shadow-card"
          : "text-ink-muted hover:bg-surface-muted hover:text-ink disabled:hover:bg-transparent disabled:hover:text-ink-muted",
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn("size-4 shrink-0", current && "text-primary")}
      />
      <span className="app-sidebar__label truncate">{label}</span>
    </button>
  );
}
