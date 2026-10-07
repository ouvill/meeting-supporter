import { cloneElement, isValidElement, useId, type ReactNode } from "react";
import { Captions, Database, Info, Mic2, Sparkles } from "lucide-react";
import type { SettingsCategory } from "./types";

export type SettingsView = "reply" | "speech" | SettingsViewCategory;
type SettingsViewCategory = Exclude<SettingsCategory, "support">;

const VIEWS: Array<{
  id: SettingsView;
  label: string;
  description: string;
  icon: typeof Info;
}> = [
  {
    id: "reply",
    label: "返答案",
    description: "使うAIとモデル",
    icon: Sparkles,
  },
  {
    id: "speech",
    label: "文字起こし",
    description: "音声認識と会議の言語",
    icon: Captions,
  },
  {
    id: "audio",
    label: "マイクと音声",
    description: "入力の選択と音量の確認",
    icon: Mic2,
  },
  {
    id: "privacy",
    label: "データと保存",
    description: "保存先と送信範囲",
    icon: Database,
  },
  {
    id: "about",
    label: "このアプリ",
    description: "ライセンス情報",
    icon: Info,
  },
];

export function SettingsNavigation({
  active,
  onChange,
}: {
  active: SettingsView;
  onChange: (view: SettingsView) => void;
}) {
  return (
    <nav
      aria-label="設定カテゴリ"
      className="grid shrink-0 grid-cols-2 gap-1 border-b border-line p-3 md:flex md:w-52 md:flex-col md:border-b-0 md:border-r md:px-3 md:py-6"
    >
      {VIEWS.map(({ id, label, description, icon: Icon }) => {
        const selected = active === id;
        return (
          <button
            key={id}
            id={`settings-nav-${id}`}
            type="button"
            aria-current={selected ? "page" : undefined}
            onClick={() => onChange(id)}
            className={`group flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2 text-left transition-colors motion-reduce:transition-none ${selected ? "bg-primary-soft text-primary" : "text-ink-muted hover:bg-surface-muted hover:text-ink"}`}
          >
            <Icon
              className={`h-4 w-4 shrink-0 ${selected ? "text-primary" : "text-ink-faint group-hover:text-ink-muted"}`}
              aria-hidden="true"
            />
            <span className="min-w-0">
              <span className="block whitespace-nowrap text-sm font-semibold">
                {label}
              </span>
              <span
                className={`mt-0.5 hidden text-xs leading-snug md:block ${selected ? "text-primary" : "text-ink-muted"}`}
              >
                {description}
              </span>
            </span>
          </button>
        );
      })}
    </nav>
  );
}

export function SettingsPage({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section
      data-settings-page={title}
      className="mx-auto w-full max-w-3xl space-y-8 [&>header+.settings-section]:border-0 [&>header+.settings-section]:pt-0"
      aria-labelledby={`settings-${title}`}
    >
      <header>
        <h3
          id={`settings-${title}`}
          className="font-display text-lg font-bold tracking-[0.01em] text-ink"
        >
          {title}
        </h3>
        <p className="mt-1 text-sm leading-relaxed text-ink-muted">
          {description}
        </p>
      </header>
      {children}
    </section>
  );
}

export function SettingsSection({
  title,
  description,
  children,
}: {
  title?: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="settings-section space-y-4 border-t border-line pt-6">
      {(title || description) && (
        <div className="mb-4">
          {title && (
            <h4 className="font-display text-sm font-bold text-ink">{title}</h4>
          )}
          {description && (
            <p className="mt-1 text-xs leading-relaxed text-ink-muted">
              {description}
            </p>
          )}
        </div>
      )}
      {children}
    </section>
  );
}

export function FieldRow({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  const labelId = useId();
  const hintId = useId();
  const errorId = useId();
  const describedBy =
    [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") ||
    undefined;
  const control = isValidElement<{
    "aria-label"?: string;
    "aria-describedby"?: string;
    "aria-labelledby"?: string;
  }>(children)
    ? cloneElement(children, {
        "aria-describedby":
          [children.props["aria-describedby"], describedBy]
            .filter(Boolean)
            .join(" ") || undefined,
        "aria-labelledby": children.props["aria-label"]
          ? children.props["aria-labelledby"]
          : [children.props["aria-labelledby"], labelId]
              .filter(Boolean)
              .join(" "),
      })
    : children;
  return (
    <div className="grid gap-1.5 md:grid-cols-[10rem_minmax(0,1fr)] md:items-start md:gap-4">
      <div>
        <p id={labelId} className="text-sm font-medium text-ink">
          {label}
        </p>
        {hint && (
          <p
            id={hintId}
            className="mt-0.5 text-xs leading-relaxed text-ink-muted"
          >
            {hint}
          </p>
        )}
      </div>
      <div>
        {control}
        {error && (
          <p
            id={errorId}
            className="mt-1.5 text-xs font-medium text-danger"
            role="alert"
          >
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

export function ToggleField({
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label
      className={`flex items-start justify-between gap-4 ${disabled ? "cursor-not-allowed opacity-55" : "cursor-pointer"}`}
    >
      <span>
        <span className="block text-sm font-semibold text-ink">{label}</span>
        <span className="mt-1 block text-xs leading-relaxed text-ink-muted">
          {description}
        </span>
      </span>
      <span className="relative mt-0.5 shrink-0">
        <input
          type="checkbox"
          className="peer sr-only"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
          aria-label={label}
        />
        <span className="block h-5 w-9 rounded-full bg-line transition-colors peer-checked:bg-primary peer-focus-visible:ring-2 peer-focus-visible:ring-primary peer-focus-visible:ring-offset-2 peer-disabled:bg-surface-muted" />
        <span className="absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-surface shadow-sm transition-transform peer-checked:translate-x-4" />
      </span>
    </label>
  );
}
