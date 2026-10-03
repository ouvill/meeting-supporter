import { X } from "lucide-react";
import { useSettingsTaskStore } from "../store/settingsTaskStore";
import { Button } from "./ui/Button";
import { InlineNotice } from "./ui/InlineNotice";

export function SettingsTaskNotice({
  onOpenSettings,
}: {
  onOpenSettings: () => void;
}) {
  const { tasks, dismiss } = useSettingsTaskStore();
  if (Object.keys(tasks).length === 0) return null;

  return (
    <div
      className="max-h-56 shrink-0 space-y-2 overflow-y-auto px-4 pt-3"
      aria-label="設定の処理状況"
    >
      {Object.values(tasks).map((task) => (
        <InlineNotice
          key={task.id}
          tone={
            task.state === "running"
              ? "info"
              : task.state === "failed"
                ? "danger"
                : "positive"
          }
          title={
            task.state === "running"
              ? task.label
              : task.state === "failed"
                ? "設定の処理を確認してください"
                : "設定の処理が完了しました"
          }
          action={
            <div className="flex items-center gap-1">
              <Button variant="quiet" size="sm" onClick={onOpenSettings}>
                設定を開く
              </Button>
              {task.state !== "running" && (
                <Button
                  variant="quiet"
                  size="icon"
                  aria-label="処理の通知を閉じる"
                  onClick={() => dismiss(task.id)}
                >
                  <X className="size-4" aria-hidden="true" />
                </Button>
              )}
            </div>
          }
        >
          {task.message}
        </InlineNotice>
      ))}
    </div>
  );
}
