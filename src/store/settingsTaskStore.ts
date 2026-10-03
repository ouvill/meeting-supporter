import { create } from "zustand";

export interface SettingsTask {
  id: string;
  label: string;
  state: "running" | "completed" | "failed";
  message: string;
}

interface SettingsTaskStore {
  tasks: Record<string, SettingsTask>;
  report: (task: SettingsTask) => void;
  dismiss: (id: string) => void;
}

// In-memory tasks belong to the application, not the settings dialog.
export const useSettingsTaskStore = create<SettingsTaskStore>((set) => ({
  tasks: {},
  report: (task) =>
    set((state) => ({ tasks: { ...state.tasks, [task.id]: task } })),
  dismiss: (id) =>
    set((state) => {
      if (state.tasks[id]?.state === "running") return state;
      const tasks = { ...state.tasks };
      delete tasks[id];
      return { tasks };
    }),
}));
