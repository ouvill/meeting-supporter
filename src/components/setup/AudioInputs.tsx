import { Mic2, Volume2 } from "lucide-react";
import type { SendFn, SocketState } from "../../types";
import { DeviceSelect } from "./DeviceSelect";
import { SystemAudioTestControl } from "./AudioPreparation";

export function AudioInputs({
  state,
  send,
  locked = false,
}: {
  state: SocketState;
  send: SendFn;
  locked?: boolean;
}) {
  const monitors = state.devices.filter((device) => device.is_monitor);
  const mics = state.devices.filter((device) => !device.is_monitor);
  const disabled =
    locked ||
    state.isRunning ||
    state.sttInitializing ||
    state.sttInitRequested ||
    !state.connected;
  return (
    <div className="divide-y divide-line">
      <DeviceSelect
        label="自分のマイク"
        icon={Mic2}
        value={state.deviceSelf}
        monitors={monitors}
        mics={mics}
        primary="mics"
        disabled={disabled}
        level={state.levelSelf}
        onChange={(device) =>
          send({ type: "set_device", role: "self", device })
        }
      />
      <div>
        <DeviceSelect
          label="相手側の音声"
          icon={Volume2}
          value={state.deviceOther}
          monitors={monitors}
          mics={mics}
          primary="monitors"
          disabled={disabled}
          level={state.levelOther}
          onChange={(device) =>
            send({ type: "set_device", role: "other", device })
          }
        />
        <SystemAudioTestControl />
      </div>
    </div>
  );
}
