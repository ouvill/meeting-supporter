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
    <div className="grid items-center gap-x-4 gap-y-2 py-3 sm:grid-cols-[9rem_minmax(0,1fr)_5.5rem]">
      <div className="flex items-center gap-2">
        <Icon
          aria-hidden="true"
          className={`size-4 shrink-0 ${primary === "monitors" ? "text-cue" : "text-positive"}`}
        />
        <label htmlFor={selectId} className="text-sm font-semibold text-ink">
          {label}
        </label>
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
      <AudioLevelMeter level={level} color={color} label={label} />
    </div>
  );
}

function AudioLevelMeter({
  level,
  color,
  label,
}: {
  level: number;
  color: string;
  label: string;
}) {
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-line"
      aria-label={`${label}の入力レベル`}
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
