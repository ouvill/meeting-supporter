import { useSyncExternalStore } from "react";
import { replyLatency } from "../../utils/replyLatency";

export function ReplyTimingDetails({
  generationId,
  suggestionId,
}: {
  generationId: string | null;
  suggestionId: string | null;
}) {
  useSyncExternalStore(replyLatency.subscribe, replyLatency.getRevision);
  const timing = replyLatency.details(generationId, suggestionId);
  const rows = [
    ["操作 → 最初の文字", timing.firstTextMs],
    ["操作 → 最初の一文", timing.firstSentenceMs],
    ["送信準備", timing.preparationMs],
    ["送信後 → 最初の文字", timing.responseWaitMs],
    ["文字開始 → 一文目", timing.sentenceStreamingMs],
  ] as const;
  return (
    <details className="shrink-0 px-1 text-xs text-ink-faint">
      <summary className="cursor-pointer py-1">
        応答時間の内訳（開発用）
      </summary>
      <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 tabular-nums">
        {rows.map(([label, ms]) => (
          <div key={label} className="contents">
            <dt>{label}</dt>
            <dd>{ms === null ? "未計測" : `${(ms / 1000).toFixed(2)}秒`}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 leading-5">
        送信準備以降の内訳は生成終了後に反映されます。送信後の待ちには通信時間も含みます。
      </p>
    </details>
  );
}
