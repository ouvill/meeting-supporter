import { client } from "../../api/generated/client.gen";
import type {
  MeetingDetail,
  MeetingListItem,
} from "../../api/generated/types.gen";
import { AppFrame } from "../product/AppFrame";
import { TooltipProvider } from "../ui/Tooltip";
import { MeetingHistoryScreen } from "./MeetingHistoryScreen";

const HOUR_MS = 60 * 60 * 1000;

function startedAt(daysAgo: number, hour: number): Date {
  const date = new Date(Date.now() - daysAgo * 24 * HOUR_MS);
  date.setHours(hour, 30, 0, 0);
  return date;
}

function listItem(
  id: string,
  title: string | null,
  daysAgo: number,
  hour: number,
  minutes: number,
  overrides: Partial<MeetingListItem> = {},
): MeetingListItem {
  const start = startedAt(daysAgo, hour);
  return {
    id,
    title,
    started_at: start.toISOString(),
    ended_at: new Date(start.getTime() + minutes * 60_000).toISOString(),
    duration_seconds: minutes * 60 + 15,
    status: "completed",
    has_ai_note: false,
    has_recording: true,
    ...overrides,
  };
}

const MEETINGS: MeetingListItem[] = [
  listItem("preview-1", "料金改定のご案内方針", 0, 10, 32),
  listItem("preview-2", "採用面接（エンジニア）", 0, 9, 48, {
    has_recording: false,
  }),
  listItem("preview-3", "週次定例", 1, 15, 25),
  listItem("preview-4", null, 4, 13, 7, { status: "aborted" }),
  listItem("preview-5", "A社 キックオフ", 9, 11, 61),
];

const TURNS = [
  ["other", "来月の料金改定について、顧客向けの説明資料も用意したいです。"],
  ["self", "変更理由と影響範囲が短く分かる資料をこちらで作ります。"],
  ["other", "助かります。来週火曜日までに初稿をいただけますか？"],
  ["self", "はい、火曜日の午前中までに共有します。"],
  ["self", "図表はこちらで用意しておきます。"],
  ["other", "既存のお客様へいつから適用するかも、合わせて確認したいです。"],
] as const;

function detail(item: MeetingListItem): MeetingDetail {
  const start = new Date(item.started_at).getTime();
  const at = (seconds: number) =>
    new Date(start + seconds * 1000).toISOString();
  return {
    ...item,
    ai_note: "",
    minutes: "",
    turns: TURNS.map(([speaker, text], index) => ({
      id: `${item.id}-turn-${index}`,
      sequence: index,
      speaker,
      text,
      created_at: at(12 + index * 47),
    })),
    reply_suggestions: [
      {
        id: `${item.id}-suggestion-0`,
        target_turn_id: `${item.id}-turn-2`,
        sequence: 0,
        agent_id: "reply",
        agent_label: "標準",
        text: "ありがとうございます。火曜日までに初稿を共有し、適用開始日は確認結果を反映します。",
        created_at: at(112),
      },
    ],
    recording_assets: item.has_recording
      ? (["other", "self"] as const).map((role) => ({
          id: `${item.id}-${role}`,
          role,
          format: "wav",
          sample_rate: 16000,
          channels: 1,
          started_at: item.started_at,
          ended_at: item.ended_at,
          size_bytes: 1024,
        }))
      : [],
  };
}

const previewFetch: typeof fetch = async (input) => {
  const url = new URL(input instanceof Request ? input.url : String(input));
  const item = MEETINGS.find(
    (meeting) => url.pathname === `/meetings/${meeting.id}`,
  );
  const body =
    url.pathname === "/meetings"
      ? { items: MEETINGS, total: MEETINGS.length, limit: 50, offset: 0 }
      : item
        ? detail(item)
        : null;
  return new Response(JSON.stringify(body ?? {}), {
    status: body ? 200 : 404,
    headers: { "Content-Type": "application/json" },
  });
};

client.setConfig({ baseUrl: "http://preview.invalid", fetch: previewFetch });

export function MeetingHistoryPreview() {
  return (
    <TooltipProvider>
      <div className="flex h-screen flex-col overflow-hidden bg-paper">
        <AppFrame
          active="reflection"
          connected
          onNavigate={() => undefined}
          onSettings={() => undefined}
        >
          <MeetingHistoryScreen onBack={() => undefined} />
        </AppFrame>
      </div>
    </TooltipProvider>
  );
}
