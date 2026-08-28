import { useId } from "react";
import { Mic2 } from "lucide-react";
import type { Device, DeviceId } from "../../types";
import { levelToPercent } from "../../utils/audioLevel";

type IconComponent = typeof Mic2;

interface DeviceSelectProps {
  label: string;
  icon: IconComponent;
  value: DeviceId;
  monitors: Device[];
  mics: Device[];
  primary: "monitors" | "mics";
  disabled: boolean;
  level: number;
  onChange: (value: DeviceId) => void;
}

export function DeviceSelect({
  label,
  icon: Icon,
  value,
  monitors,
  mics,
  primary,
  disabled,
  level,
  onChange,
}: DeviceSelectProps) {
  const selectId = useId();
  const first = primary === "monitors" ? monitors : mics;
  const second = primary === "monitors" ? mics : monitors;
  const firstLabel = primary === "monitors" ? "スピーカー" : "マイク";
  const secondLabel = primary === "monitors" ? "マイク" : "スピーカー";
  const defaultDevice =
    first.find((device) => device.is_default) ??
    second.find((device) => device.is_default);
  const defaultLabel = primary === "monitors" ? "既定スピーカー" : "既定マイク";
  const defaultOptionLabel = defaultDevice
    ? `${defaultLabel}（${defaultDevice.name}）`
    : defaultLabel;
  const color = primary === "monitors" ? "bg-cue" : "bg-positive";

  function handleChange(rawValue: string) {
    if (!rawValue) {
      onChange(null);
      return;
    }
    const numericValue = Number(rawValue);
    onChange(Number.isNaN(numericValue) ? rawValue : numericValue);
  }

  return (
    <div className="rounded-xl bg-paper p-3">
      <div className="mb-2 flex items-center gap-2">
        <Icon
          aria-hidden="true"
          size={15}
          className={primary === "monitors" ? "text-cue" : "text-positive"}
        />
        <label htmlFor={selectId} className="text-sm font-semibold text-ink">
          {label}
        </label>
        <AudioLevelMeter level={level} color={color} />
      </div>
      <select
        id={selectId}
        value={value === null || value === undefined ? "" : String(value)}
        onChange={(event) => handleChange(event.target.value)}
        disabled={disabled}
        className="field text-sm"
      >
        <option value="">{defaultOptionLabel}</option>
        {first.length > 0 && (
          <optgroup label={firstLabel}>
            {first.map((device) => (
              <option key={String(device.index)} value={String(device.index)}>
                {device.name}
              </option>
            ))}
          </optgroup>
        )}
        {second.length > 0 && (
          <optgroup label={secondLabel}>
            {second.map((device) => (
              <option key={String(device.index)} value={String(device.index)}>
                {device.name}
              </option>
            ))}
          </optgroup>
        )}
      </select>
    </div>
  );
}

function AudioLevelMeter({ level, color }: { level: number; color: string }) {
  return (
    <div
      className="ml-auto h-1.5 w-24 overflow-hidden rounded-full bg-line"
      aria-label={`入力レベル ${Math.round(levelToPercent(level))}%`}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(levelToPercent(level))}
    >
      <div
        className={`h-full rounded-full ${color} transition-[width] duration-75 motion-reduce:transition-none`}
        style={{ width: `${levelToPercent(level)}%` }}
      />
    </div>
  );
}
