import { create } from "zustand";
import { getAgentCatalog, type AgentCatalog } from "../api/agentRegistry";
import { useSettingsTaskStore } from "./settingsTaskStore";

interface AgentRegistryState {
  catalog: AgentCatalog | null;
  catalogError: string | null;
  pending: string | null;
  error: string | null;
  message: string | null;
  revision: number;
  refresh: (signal: AbortSignal, force?: boolean) => Promise<void>;
  perform: (
    label: string,
    action: () => Promise<void>,
    onChanged: () => void,
  ) => Promise<void>;
}

export const useAgentRegistryStore = create<AgentRegistryState>((set, get) => ({
  catalog: null,
  catalogError: null,
  pending: null,
  error: null,
  message: null,
  revision: 0,
  refresh: async (signal, force = false) => {
    if (get().pending) return;
    const revision = get().revision;
    try {
      const catalog = await getAgentCatalog(force, signal);
      if (!signal.aborted && revision === get().revision)
        set({ catalog, catalogError: null });
    } catch {
      if (!signal.aborted && revision === get().revision)
        set({ catalogError: "エージェントの一覧を取得できませんでした。" });
    }
  },
  perform: async (label, action, onChanged) => {
    if (get().pending) return;
    set({
      pending: label,
      error: null,
      message: null,
      revision: get().revision + 1,
    });
    const report = useSettingsTaskStore.getState().report;
    report({
      id: "agents",
      label,
      state: "running",
      message: "設定を閉じても処理は続きます。",
    });
    try {
      await action();
    } catch (cause) {
      set({
        error: cause instanceof Error ? cause.message : "操作に失敗しました。",
      });
    } finally {
      // Refresh even after a partial failure, including while settings is closed.
      try {
        set({ catalog: await getAgentCatalog() });
      } catch {
        set({
          error:
            get().error ??
            "処理後の状態を取得できませんでした。設定を開いて再確認してください。",
        });
      }
      const { error, message } = get();
      set({ pending: null });
      report({
        id: "agents",
        label,
        state: error ? "failed" : "completed",
        message: error ?? message ?? "処理が完了しました。",
      });
      onChanged();
    }
  },
}));
