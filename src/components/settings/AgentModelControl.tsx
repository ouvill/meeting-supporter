import { useEffect } from "react";
import {
  selectAgentModel,
  selectAgentThoughtLevel,
} from "../../api/agentRegistry";
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
  const thoughtLevel = agent?.status.thought_level;
  return (
    <>
      {model && agent ? (
        <FieldRow label="モデル" hint="変更はすぐに反映されます">
          <select
            className="field"
            aria-label={`${agent.name}のモデル`}
            value={model.current}
            disabled={locked || pending !== null || !agent.status.ready}
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
      {thoughtLevel && agent?.status.ready && (
        <FieldRow
          label="推論量"
          hint="少ない推論量は応答速度、多い推論量は検討の深さを重視します。変更はすぐに反映され、次の返答案から使われます。"
        >
          <select
            className="field"
            aria-label={`${agent.name}の推論量`}
            value={thoughtLevel.current}
            disabled={locked || pending !== null}
            onChange={(event) => {
              const value = event.target.value;
              void perform(
                "推論量を変更しています",
                async () => {
                  await selectAgentThoughtLevel(agent.id, value);
                },
                onChanged,
              );
            }}
          >
            {thoughtLevel.options.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>
        </FieldRow>
      )}
      {error && <InlineNotice tone="danger">{error}</InlineNotice>}
    </>
  );
}
