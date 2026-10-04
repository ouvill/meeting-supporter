import { useEffect } from "react";
import { useAgentRegistryStore } from "../../store/agentRegistryStore";
import type { AgentModelDraftsController } from "./useAgentModelDrafts";
import { FieldRow } from "./SettingsPrimitives";
import { InlineNotice } from "../ui";
import { Button } from "../ui/Button";

export function AgentModelControl({
  routeId,
  locked,
  settings,
}: {
  routeId: string;
  locked: boolean;
  settings: AgentModelDraftsController;
}) {
  const { catalog, refresh, pending, catalogError } = useAgentRegistryStore();
  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [routeId, refresh]);
  const agent = catalog?.agents.find((item) => `acp:${item.id}` === routeId);
  const model = agent?.status.model;
  const thoughtLevel = agent?.status.thought_level;
  const draft = agent ? settings.drafts[agent.id] : undefined;
  return (
    <>
      {model && agent ? (
        <FieldRow label="モデル">
          <select
            className="field"
            aria-label={`${agent.name}のモデル`}
            value={draft?.model ?? model.current}
            disabled={locked || pending !== null || !agent.status.ready}
            onChange={(event) => {
              const value = event.target.value;
              settings.change(agent.id, "model", value);
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
          hint={
            draft?.model
              ? "モデルを保存すると、対応する推論量を選べます。"
              : "少ない推論量は応答速度、多い推論量は検討の深さを重視します。"
          }
        >
          <select
            className="field"
            aria-label={`${agent.name}の推論量`}
            value={draft?.thoughtLevel ?? thoughtLevel.current}
            disabled={locked || pending !== null || draft?.model !== undefined}
            onChange={(event) => {
              const value = event.target.value;
              settings.change(agent.id, "thoughtLevel", value);
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
      {settings.error && (
        <InlineNotice tone="danger">{settings.error}</InlineNotice>
      )}
      {catalogError && (
        <InlineNotice tone="danger">
          <p>{catalogError}</p>
          <Button
            size="sm"
            variant="secondary"
            className="mt-3"
            disabled={pending !== null}
            onClick={() => void refresh(new AbortController().signal)}
          >
            再試行
          </Button>
        </InlineNotice>
      )}
    </>
  );
}
