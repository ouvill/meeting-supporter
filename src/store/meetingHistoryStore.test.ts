import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useMeetingHistoryStore } from "./meetingHistoryStore";
import type {
  ListMeetingsMeetingsGetResponse,
  GetMeetingMeetingsMeetingIdGetResponse,
  UpdateMeetingTitleMeetingsMeetingIdPatchResponse,
  DeleteMeetingMeetingsMeetingIdDeleteResponse,
  MeetingListItem,
  MeetingDetail,
} from "../api/generated/types.gen";

// ── Mocks ────────────────────────────────────────────────────────

const mockList = vi.fn();
const mockGet = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();

vi.mock("../api/generated/sdk.gen", () => ({
  listMeetingsMeetingsGet: (...args: unknown[]) => mockList(...args),
  getMeetingMeetingsMeetingIdGet: (...args: unknown[]) => mockGet(...args),
  updateMeetingTitleMeetingsMeetingIdPatch: (...args: unknown[]) =>
    mockPatch(...args),
  deleteMeetingMeetingsMeetingIdDelete: (...args: unknown[]) =>
    mockDelete(...args),
}));

// ── Fixtures ─────────────────────────────────────────────────────

function makeMeeting(
  id: string,
  overrides: Partial<MeetingListItem> = {},
): MeetingListItem {
  return {
    id,
    title: null,
    started_at: "2026-06-01T10:00:00Z",
    status: "completed",
    duration_seconds: null,
    ended_at: null,
    has_ai_note: false,
    has_recording: false,
    ...overrides,
  };
}

const meeting1 = makeMeeting("m1", { title: "会議1", duration_seconds: 600 });
const meeting2 = makeMeeting("m2", {
  title: "会議2",
  duration_seconds: 1200,
  status: "aborted",
});
const meeting3 = makeMeeting("m3", { title: "会議3", duration_seconds: 1800 });

function makePage(
  items: MeetingListItem[],
  overrides: Partial<ListMeetingsMeetingsGetResponse> = {},
): ListMeetingsMeetingsGetResponse {
  return {
    items,
    total: items.length,
    limit: 50,
    offset: 0,
    ...overrides,
  };
}

function makeDetail(
  base: MeetingListItem,
  overrides: Partial<MeetingDetail> = {},
): MeetingDetail {
  return {
    id: base.id,
    title: base.title,
    started_at: base.started_at,
    status: base.status,
    duration_seconds: base.duration_seconds,
    ended_at: base.ended_at,
    ai_note: "",
    minutes: "",
    turns: [],
    reply_suggestions: [],
    recording_assets: [],
    ...overrides,
  };
}

function okResponse<T>(data: T): { data: T; error: undefined } {
  return { data, error: undefined };
}

function errorResponse(): { data: undefined; error: { message: string } } {
  return { data: undefined, error: { message: "API error" } };
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

// ── Tests ────────────────────────────────────────────────────────

describe("meetingHistoryStore", () => {
  beforeEach(() => {
    useMeetingHistoryStore.getState().reset();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("initial state", () => {
    const s = useMeetingHistoryStore.getState();
    expect(s.meetings).toEqual([]);
    expect(s.selectedMeetingId).toBeNull();
    expect(s.selectedMeeting).toBeNull();
    expect(s.total).toBe(0);
    expect(s.hasMore).toBe(false);
    expect(s.loading).toBe(false);
    expect(s.loadingDetail).toBe(false);
    expect(s.loadingMore).toBe(false);
    expect(s.error).toBeNull();
    expect(s.saving).toBe(false);
    expect(s.deleting).toBe(false);
  });

  it("loadMeetings sets meetings and selects first item when none selected", async () => {
    mockList.mockResolvedValueOnce(
      okResponse<ListMeetingsMeetingsGetResponse>(
        makePage([meeting1, meeting2]),
      ),
    );
    const detail1 = makeDetail(meeting1);
    mockGet.mockResolvedValueOnce(
      okResponse<GetMeetingMeetingsMeetingIdGetResponse>(detail1),
    );

    await useMeetingHistoryStore.getState().loadMeetings();

    const s = useMeetingHistoryStore.getState();
    expect(s.meetings).toHaveLength(2);
    expect(s.loading).toBe(false);
    expect(s.error).toBeNull();
    expect(s.total).toBe(2);
    expect(s.hasMore).toBe(false);
    expect(s.selectedMeetingId).toBe("m1");
    expect(s.selectedMeeting?.id).toBe("m1");
    expect(mockGet).toHaveBeenCalledTimes(1);
  });

  it("loadMeetings keeps existing selection if still valid", async () => {
    // Pre-select meeting2
    useMeetingHistoryStore.setState({ selectedMeetingId: "m2" });
    mockList.mockResolvedValueOnce(
      okResponse<ListMeetingsMeetingsGetResponse>(
        makePage([meeting1, meeting2]),
      ),
    );
    const detail2 = makeDetail(meeting2);
    mockGet.mockResolvedValueOnce(
      okResponse<GetMeetingMeetingsMeetingIdGetResponse>(detail2),
    );

    await useMeetingHistoryStore.getState().loadMeetings();

    const s = useMeetingHistoryStore.getState();
    expect(s.selectedMeetingId).toBe("m2");
    expect(s.selectedMeeting?.id).toBe("m2");
  });

  it("loadMeetings clears selection when no meetings exist", async () => {
    mockList.mockResolvedValueOnce(
      okResponse<ListMeetingsMeetingsGetResponse>(makePage([])),
    );

    await useMeetingHistoryStore.getState().loadMeetings();

    const s = useMeetingHistoryStore.getState();
    expect(s.meetings).toHaveLength(0);
    expect(s.selectedMeetingId).toBeNull();
    expect(s.selectedMeeting).toBeNull();
  });

  it("loadMore appends the next page and preserves existing selection", async () => {
    useMeetingHistoryStore.setState({
      meetings: [meeting1, meeting2],
      selectedMeetingId: "m1",
      selectedMeeting: makeDetail(meeting1),
      total: 3,
      hasMore: true,
    });
    mockList.mockResolvedValueOnce(
      okResponse<ListMeetingsMeetingsGetResponse>(
        makePage([meeting3], { total: 3, limit: 50, offset: 2 }),
      ),
    );

    await useMeetingHistoryStore.getState().loadMore();

    const s = useMeetingHistoryStore.getState();
    expect(mockList).toHaveBeenCalledWith({ query: { limit: 50, offset: 2 } });
    expect(s.meetings.map((m) => m.id)).toEqual(["m1", "m2", "m3"]);
    expect(s.selectedMeetingId).toBe("m1");
    expect(s.hasMore).toBe(false);
    expect(s.loadingMore).toBe(false);
  });

  it("loadMore deduplicates meetings returned across page boundaries", async () => {
    useMeetingHistoryStore.setState({
      meetings: [meeting1, meeting2],
      total: 3,
      hasMore: true,
    });
    mockList.mockResolvedValueOnce(
      okResponse<ListMeetingsMeetingsGetResponse>(
        makePage([meeting2, meeting3], { total: 3, limit: 50, offset: 2 }),
      ),
    );

    await useMeetingHistoryStore.getState().loadMore();

    expect(useMeetingHistoryStore.getState().meetings.map((m) => m.id)).toEqual(
      ["m1", "m2", "m3"],
    );
  });

  it("loadMeetings sets error string on API error", async () => {
    mockList.mockResolvedValueOnce(errorResponse());

    await useMeetingHistoryStore.getState().loadMeetings();

    const s = useMeetingHistoryStore.getState();
    expect(s.loading).toBe(false);
    expect(s.error).toBeTruthy();
    expect(s.meetings).toEqual([]);
  });

  it("selectMeeting loads detail", async () => {
    useMeetingHistoryStore.setState({ meetings: [meeting1] });
    const detail = makeDetail(meeting1, { ai_note: "# Summary" });
    mockGet.mockResolvedValueOnce(
      okResponse<GetMeetingMeetingsMeetingIdGetResponse>(detail),
    );

    await useMeetingHistoryStore.getState().selectMeeting("m1");

    const s = useMeetingHistoryStore.getState();
    expect(s.selectedMeetingId).toBe("m1");
    expect(s.selectedMeeting?.ai_note).toBe("# Summary");
    expect(s.loadingDetail).toBe(false);
  });

  it("selectMeeting handles API error", async () => {
    mockGet.mockResolvedValueOnce(errorResponse());

    await useMeetingHistoryStore.getState().selectMeeting("m1");

    const s = useMeetingHistoryStore.getState();
    expect(s.loadingDetail).toBe(false);
    expect(s.error).toBeTruthy();
    expect(s.selectedMeeting).toBeNull();
  });

  it("selectMeeting retains the later detail when an earlier successful request resolves last", async () => {
    const staleResponse = deferred<unknown>();
    const latestDetail = makeDetail(meeting2, { ai_note: "最新の詳細" });
    mockGet.mockImplementationOnce(() => staleResponse.promise);
    mockGet.mockResolvedValueOnce(
      okResponse<GetMeetingMeetingsMeetingIdGetResponse>(latestDetail),
    );

    const earlierSelection = useMeetingHistoryStore
      .getState()
      .selectMeeting("m1");
    const laterSelection = useMeetingHistoryStore
      .getState()
      .selectMeeting("m2");
    await laterSelection;
    staleResponse.resolve(
      okResponse<GetMeetingMeetingsMeetingIdGetResponse>(
        makeDetail(meeting1, {
          ai_note: "古い詳細",
        }),
      ),
    );
    await earlierSelection;

    expect(useMeetingHistoryStore.getState()).toMatchObject({
      selectedMeetingId: "m2",
      selectedMeeting: expect.objectContaining({
        id: "m2",
        ai_note: "最新の詳細",
      }),
      loadingDetail: false,
    });
  });

  it("selectMeeting retains the later detail when an earlier request fails last", async () => {
    const staleResponse = deferred<unknown>();
    const latestDetail = makeDetail(meeting2, { ai_note: "最新の詳細" });
    mockGet.mockImplementationOnce(() => staleResponse.promise);
    mockGet.mockResolvedValueOnce(
      okResponse<GetMeetingMeetingsMeetingIdGetResponse>(latestDetail),
    );

    const earlierSelection = useMeetingHistoryStore
      .getState()
      .selectMeeting("m1");
    const laterSelection = useMeetingHistoryStore
      .getState()
      .selectMeeting("m2");
    await laterSelection;
    staleResponse.reject(new Error("earlier detail request failed"));
    await earlierSelection;

    expect(useMeetingHistoryStore.getState()).toMatchObject({
      selectedMeetingId: "m2",
      selectedMeeting: expect.objectContaining({
        id: "m2",
        ai_note: "最新の詳細",
      }),
      loadingDetail: false,
      error: null,
    });
  });

  it("loadMeetings retains the later page when an earlier successful request resolves last", async () => {
    const staleResponse = deferred<unknown>();
    mockList.mockImplementationOnce(() => staleResponse.promise);
    mockList.mockResolvedValueOnce(
      okResponse<ListMeetingsMeetingsGetResponse>(makePage([meeting2])),
    );
    mockGet.mockResolvedValueOnce(
      okResponse<GetMeetingMeetingsMeetingIdGetResponse>(makeDetail(meeting2)),
    );

    const earlierLoad = useMeetingHistoryStore.getState().loadMeetings();
    const laterLoad = useMeetingHistoryStore.getState().loadMeetings();
    await laterLoad;
    staleResponse.resolve(
      okResponse<ListMeetingsMeetingsGetResponse>(makePage([meeting1])),
    );
    await earlierLoad;

    expect(useMeetingHistoryStore.getState()).toMatchObject({
      meetings: [meeting2],
      selectedMeetingId: "m2",
      selectedMeeting: expect.objectContaining({ id: "m2" }),
      loading: false,
    });
  });

  it("loadMeetings retains the later page when an earlier request fails last", async () => {
    const staleResponse = deferred<unknown>();
    mockList.mockImplementationOnce(() => staleResponse.promise);
    mockList.mockResolvedValueOnce(
      okResponse<ListMeetingsMeetingsGetResponse>(makePage([meeting2])),
    );
    mockGet.mockResolvedValueOnce(
      okResponse<GetMeetingMeetingsMeetingIdGetResponse>(makeDetail(meeting2)),
    );

    const earlierLoad = useMeetingHistoryStore.getState().loadMeetings();
    const laterLoad = useMeetingHistoryStore.getState().loadMeetings();
    await laterLoad;
    staleResponse.reject(new Error("earlier list request failed"));
    await earlierLoad;

    expect(useMeetingHistoryStore.getState()).toMatchObject({
      meetings: [meeting2],
      selectedMeetingId: "m2",
      selectedMeeting: expect.objectContaining({ id: "m2" }),
      loading: false,
      error: null,
    });
  });

  it("updateTitle updates title in detail and list", async () => {
    useMeetingHistoryStore.setState({
      meetings: [meeting1],
      selectedMeetingId: "m1",
      selectedMeeting: makeDetail(meeting1),
    });
    mockPatch.mockResolvedValueOnce(
      okResponse<UpdateMeetingTitleMeetingsMeetingIdPatchResponse>({
        ok: true,
      }),
    );

    await useMeetingHistoryStore.getState().updateTitle("m1", "新しいタイトル");

    const s = useMeetingHistoryStore.getState();
    expect(s.saving).toBe(false);
    expect(s.selectedMeeting?.title).toBe("新しいタイトル");
    expect(s.meetings[0].title).toBe("新しいタイトル");
  });

  it("updateTitle handles API error", async () => {
    mockPatch.mockResolvedValueOnce(errorResponse());

    await useMeetingHistoryStore.getState().updateTitle("m1", "新しいタイトル");

    const s = useMeetingHistoryStore.getState();
    expect(s.saving).toBe(false);
    expect(s.error).toBeTruthy();
  });

  it("deleteMeeting removes meeting and refreshes list", async () => {
    useMeetingHistoryStore.setState({
      meetings: [meeting1, meeting2],
      selectedMeetingId: "m1",
      selectedMeeting: makeDetail(meeting1),
    });
    mockDelete.mockResolvedValueOnce(
      okResponse<DeleteMeetingMeetingsMeetingIdDeleteResponse>({ ok: true }),
    );
    // After delete, list now returns only meeting2
    mockList.mockResolvedValueOnce(
      okResponse<ListMeetingsMeetingsGetResponse>(makePage([meeting2])),
    );
    const detail2 = makeDetail(meeting2);
    mockGet.mockResolvedValueOnce(
      okResponse<GetMeetingMeetingsMeetingIdGetResponse>(detail2),
    );

    await useMeetingHistoryStore.getState().deleteMeeting("m1");

    const s = useMeetingHistoryStore.getState();
    expect(s.deleting).toBe(false);
    expect(s.meetings).toHaveLength(1);
    expect(s.meetings[0].id).toBe("m2");
    expect(s.selectedMeetingId).toBe("m2");
  });

  it("deleteMeeting refreshes and clears selection when no meetings remain", async () => {
    useMeetingHistoryStore.setState({
      meetings: [meeting1],
      selectedMeetingId: "m1",
      selectedMeeting: makeDetail(meeting1),
    });
    mockDelete.mockResolvedValueOnce(
      okResponse<DeleteMeetingMeetingsMeetingIdDeleteResponse>({ ok: true }),
    );
    mockList.mockResolvedValueOnce(
      okResponse<ListMeetingsMeetingsGetResponse>(makePage([])),
    );

    await useMeetingHistoryStore.getState().deleteMeeting("m1");

    const s = useMeetingHistoryStore.getState();
    expect(s.meetings).toHaveLength(0);
    expect(s.selectedMeetingId).toBeNull();
    expect(s.selectedMeeting).toBeNull();
  });

  it("deleteMeeting handles API error", async () => {
    mockDelete.mockResolvedValueOnce(errorResponse());

    await useMeetingHistoryStore.getState().deleteMeeting("m1");

    const s = useMeetingHistoryStore.getState();
    expect(s.deleting).toBe(false);
    expect(s.error).toBeTruthy();
  });

  it("reset restores initial state", () => {
    useMeetingHistoryStore.setState({
      meetings: [meeting1],
      selectedMeetingId: "m1",
      selectedMeeting: makeDetail(meeting1),
      loading: true,
      error: "some error",
    });

    useMeetingHistoryStore.getState().reset();

    const s = useMeetingHistoryStore.getState();
    expect(s.meetings).toEqual([]);
    expect(s.selectedMeetingId).toBeNull();
    expect(s.selectedMeeting).toBeNull();
    expect(s.loading).toBe(false);
    expect(s.error).toBeNull();
  });
});
