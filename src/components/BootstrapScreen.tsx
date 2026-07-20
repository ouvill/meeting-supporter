import { AlertTriangle, LoaderCircle, Mic } from "lucide-react";
import type { BackendCrashInfo } from "../platform/backendBootstrapClient";
import { InlineNotice } from "./ui/InlineNotice";
import { Surface } from "./ui/Surface";

interface Props {
  phase: string;
  message: string;
  crashInfo?: BackendCrashInfo | null;
}

export function BootstrapScreen(props: Props) {
  const { phase, crashInfo } = props;
  const failed = phase === "failed";
  const unexpectedlyStopped = failed && crashInfo?.unexpected === true;

  return (
    <main
      data-testid="bootstrap-screen"
      className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto p-4 sm:p-8"
    >
      <Surface className="w-full max-w-md overflow-hidden">
        <div className="flex flex-col items-center px-6 py-8 text-center sm:px-9 sm:py-10">
          <div
            className={`relative flex size-16 items-center justify-center rounded-2xl border ${failed ? "border-danger/20 bg-danger-soft text-danger" : "border-primary/20 bg-primary-soft text-primary"}`}
          >
            {failed ? (
              <AlertTriangle aria-hidden="true" className="size-7" />
            ) : (
              <>
                <Mic aria-hidden="true" className="size-7" />
                <LoaderCircle
                  aria-hidden="true"
                  className="absolute -right-2 -top-2 size-5 animate-spin rounded-full bg-surface"
                />
              </>
            )}
          </div>

          <p className="mt-5 font-display text-lg font-bold tracking-[0.02em] text-ink">
            {failed
              ? unexpectedlyStopped
                ? "会議サポートが停止しました"
                : "起動できませんでした"
              : "準備しています…"}
          </p>
          <p className="mt-2 max-w-sm text-sm leading-relaxed text-ink-muted">
            {failed
              ? "安全のため処理を停止しました。アプリを終了して、もう一度開いてください。"
              : "会議の準備を整えています。このまま少しお待ちください。"}
          </p>
        </div>

        {failed && (
          <div className="border-t border-line p-4">
            <InlineNotice tone="warning" title="もう一度試すには">
              問題が続く場合はアプリを再起動してください。入力した内容は送信されていません。
            </InlineNotice>
          </div>
        )}
      </Surface>
    </main>
  );
}
