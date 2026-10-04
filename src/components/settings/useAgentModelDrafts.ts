import { useEffect, useState } from "react";
import {
  selectAgentModel,
  selectAgentThoughtLevel,
} from "../../api/agentRegistry";
import { useAgentRegistryStore } from "../../store/agentRegistryStore";

export interface AgentModelDraft {
  model?: string;
  thoughtLevel?: string;
}

export function useAgentModelDrafts(locked: boolean) {
  const [drafts, setDrafts] = useState<Record<string, AgentModelDraft>>({});
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (locked) {
      setDrafts({});
      setError(null);
    }
  }, [locked]);

  const change = (id: string, field: keyof AgentModelDraft, value: string) => {
    if (locked || useAgentRegistryStore.getState().pending) return;
    const agent = useAgentRegistryStore
      .getState()
      .catalog?.agents.find((item) => item.id === id);
    if (!agent?.status.ready) return;
    const confirmed =
      field === "model"
        ? agent.status.model?.current
        : agent.status.thought_level?.current;
    setDrafts((previous) => {
      const draft = { ...previous[id] };
      if (value === confirmed) delete draft[field];
      else draft[field] = value;
      // Reasoning choices belong to the confirmed model. Fetch new choices on save.
      if (field === "model") delete draft.thoughtLevel;
      const next = { ...previous };
      if (Object.keys(draft).length) next[id] = draft;
      else delete next[id];
      return next;
    });
    setError(null);
  };

  const save = async (): Promise<boolean> => {
    if (!Object.keys(drafts).length) return true;
    if (locked || useAgentRegistryStore.getState().pending) {
      setError("AIの処理が終わってから、もう一度保存してください。");
      return false;
    }
    setError(null);
    let saved = false;
    await useAgentRegistryStore.getState().perform(
      "AIの設定を保存しています",
      async () => {
        for (const [id, draft] of Object.entries(drafts)) {
          const agent = useAgentRegistryStore
            .getState()
            .catalog?.agents.find((item) => item.id === id);
          if (!agent?.status.ready)
            throw new Error("AIに接続してから、もう一度保存してください。");
          for (const field of ["model", "thoughtLevel"] as const) {
            const value = draft[field];
            if (value === undefined) continue;
            const status =
              field === "model"
                ? await selectAgentModel(id, value)
                : await selectAgentThoughtLevel(id, value);
            useAgentRegistryStore.setState((state) => ({
              catalog: state.catalog
                ? {
                    ...state.catalog,
                    agents: state.catalog.agents.map((item) =>
                      item.id === id ? { ...item, status } : item,
                    ),
                  }
                : null,
            }));
            // A later failure must not cause already saved changes to be retried.
            setDrafts((previous) => {
              const next = { ...previous };
              const remaining = { ...next[id] };
              delete remaining[field];
              if (Object.keys(remaining).length) next[id] = remaining;
              else delete next[id];
              return next;
            });
          }
        }
        saved = true;
      },
      () => {},
    );
    if (!saved)
      setError(
        useAgentRegistryStore.getState().error ??
          "AIの設定を保存できませんでした。",
      );
    return saved;
  };

  return {
    drafts,
    dirty: Object.keys(drafts).length > 0,
    error,
    change,
    save,
    discard: () => {
      setDrafts({});
      setError(null);
    },
  };
}

export type AgentModelDraftsController = ReturnType<typeof useAgentModelDrafts>;
