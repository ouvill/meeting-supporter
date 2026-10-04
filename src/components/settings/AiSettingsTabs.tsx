import { useRef } from "react";

export type AiSettingsTab = "reply" | "speech";
const TABS = [
  { id: "reply", label: "返答案" },
  { id: "speech", label: "文字起こし" },
] as const;

export function AiSettingsTabs({
  active,
  onChange,
}: {
  active: AiSettingsTab;
  onChange: (tab: AiSettingsTab) => void;
}) {
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);
  return (
    <div
      role="tablist"
      aria-label="AIと音声認識の設定"
      className="flex gap-1 border-b border-line"
    >
      {TABS.map(({ id, label }, index) => (
        <button
          key={id}
          type="button"
          role="tab"
          id={`ai-settings-tab-${id}`}
          aria-controls={`ai-settings-panel-${id}`}
          aria-selected={active === id}
          tabIndex={active === id ? 0 : -1}
          ref={(node) => {
            buttons.current[index] = node;
          }}
          className={`border-b-2 px-4 py-3 text-sm font-semibold ${active === id ? "border-primary text-primary" : "border-transparent text-ink-muted hover:text-ink"}`}
          onClick={() => onChange(id)}
          onKeyDown={(event) => {
            let next: number;
            if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
            else if (event.key === "ArrowLeft")
              next = (index + TABS.length - 1) % TABS.length;
            else if (event.key === "Home") next = 0;
            else if (event.key === "End") next = TABS.length - 1;
            else return;
            event.preventDefault();
            onChange(TABS[next].id);
            buttons.current[next]?.focus();
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
