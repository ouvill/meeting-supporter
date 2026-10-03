import { useEffect } from "react";
import { selectAgentModel } from "../../api/agentRegistry";
import { useAgentRegistryStore } from "../../store/agentRegistryStore";
import { FieldRow } from "./SettingsPrimitives";
import { InlineNotice } from "../ui";

export function AgentModelControl({
  routeId,
  locked,
  onChanged,
}: {
  routeId: string;
  locked: boolean;
  onChanged: () => void;
}) {
  const { catalog, refresh, pending, perform, error } = useAgentRegistryStore();
  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [routeId, refresh]);
  const agent = catalog?.agents.find((item) => `acp:${item.id}` === routeId);
  const model = agent?.status.model;
  return (
    <>
      {model && agent ? (
        <FieldRow label="モデル" hint="変更はすぐに反映されます">
          <select
            className="field"
            aria-label={`${agent.name}のモデル`}
            value={model.current}
            disabled={locked || pending !== null}
            onChange={(event) => {
              const value = event.target.value;
              void perform(
                "モデルを変更しています",
                async () => {
                  await selectAgentModel(agent.id, value);
                },
                onChanged,
              );
            }}
          >
            {model.options.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>
        </FieldRow>
      ) : (
        <p className="text-sm text-ink-muted">
          {agent?.status.ready
            ? "モデルは接続先で管理されています。"
            : "エージェントを接続すると、利用できるモデルを確認できます。"}
        </p>
      )}
      {error && <InlineNotice tone="danger">{error}</InlineNotice>}
    </>
  );
}
