import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  RouteCatalogResponse,
  RouteReadModel,
} from "../api/generated/types.gen";
import { resolveUseCaseRouteStatus, useAiRoutes } from "./useAiRoutes";

const sdkMocks = vi.hoisted(() => ({
  getAiRoutes: vi.fn(),
  replaceAiRouteAssignments: vi.fn(),
}));

vi.mock("../api/generated/sdk.gen", () => ({
  getAiRoutesApiAiRoutesGet: sdkMocks.getAiRoutes,
  replaceAiRouteAssignmentsApiAiRoutesAssignmentsPut:
    sdkMocks.replaceAiRouteAssignments,
}));

const LOAD_ERROR =
  "支援方法の状態を確認できませんでした。しばらくしてから再度お試しください。";
const SAVE_ERROR =
  "支援方法を保存できませんでした。選択内容を確認して再度お試しください。";

let visibilityState: DocumentVisibilityState;

function route(overrides: Partial<RouteReadModel> = {}): RouteReadModel {
  return {
    id: "ollama",
    kind: "local",
    label: "Ollama",
    description: "Local model",
    availability: "experimental",
    readiness: "ready",
    selectable: true,
    selected: false,
    data_location: "external",
    billing_owner: "external_subscription",
    capabilities: ["reply"],
    reason_code: null,
    message: "",
    action: "none",
    ...overrides,
  };
}

function catalog(
  overrides: Partial<RouteCatalogResponse> = {},
): RouteCatalogResponse {
  return {
    routes: [route()],
    assignments: { reply: "ollama" },
    ...overrides,
  };
}

function apiResult(data: RouteCatalogResponse) {
  return { data, error: undefined };
}

async function waitForInitialCatalog() {
  await waitFor(() => expect(sdkMocks.getAiRoutes).toHaveBeenCalledTimes(1));
}

describe("resolveUseCaseRouteStatus", () => {
  it("only enables generation for a selectable ready reply route", () => {
    expect(
      resolveUseCaseRouteStatus("reply", {
        loading: false,
        error: null,
        assignedRouteId: "ollama",
        selectedRoute: route(),
      }),
    ).toEqual({ readiness: "ready", canGenerate: true, message: null });
  });

  it.each([
    {
      name: "loading",
      input: {
        loading: true,
        error: null,
        assignedRouteId: null,
        selectedRoute: null,
      },
      expected: { readiness: "unknown", canGenerate: false, message: null },
    },
    {
      name: "catalog error",
      input: {
        loading: false,
        error: "unsafe detail",
        assignedRouteId: null,
        selectedRoute: null,
      },
      expected: { readiness: "error", canGenerate: false, message: LOAD_ERROR },
    },
    {
      name: "missing assignment",
      input: {
        loading: false,
        error: null,
        assignedRouteId: null,
        selectedRoute: null,
      },
      expected: {
        readiness: "setup_required",
        canGenerate: false,
        message: "返答案を利用する支援方法を設定してください。",
      },
    },
    {
      name: "missing reply capability",
      input: {
        loading: false,
        error: null,
        assignedRouteId: "ollama",
        selectedRoute: route({ capabilities: [] }),
      },
      expected: {
        readiness: "unavailable",
        canGenerate: false,
        message: "選択した支援方法では返答案を利用できません。",
      },
    },
    {
      name: "ready but not selectable",
      input: {
        loading: false,
        error: null,
        assignedRouteId: "ollama",
        selectedRoute: route({ selectable: false }),
      },
      expected: {
        readiness: "unavailable",
        canGenerate: false,
        message: "選択した支援方法では返答案を利用できません。",
      },
    },
    {
      name: "route setup required",
      input: {
        loading: false,
        error: null,
        assignedRouteId: "ollama",
        selectedRoute: route({
          readiness: "setup_required",
          message: "ログインしてください",
        }),
      },
      expected: {
        readiness: "setup_required",
        canGenerate: false,
        message: "ログインしてください",
      },
    },
  ])("resolves $name without overstating readiness", ({ input, expected }) => {
    expect(resolveUseCaseRouteStatus("reply", input)).toEqual(expected);
  });
});

describe("useAiRoutes", () => {
  beforeEach(() => {
    sdkMocks.getAiRoutes.mockReset();
    sdkMocks.replaceAiRouteAssignments.mockReset();
    visibilityState = "visible";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(
      () => visibilityState,
    );
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("loads the initial catalog and exposes its assigned selected route", async () => {
    sdkMocks.getAiRoutes.mockResolvedValue(apiResult(catalog()));

    const { result } = renderHook(() => useAiRoutes());

    await waitForInitialCatalog();
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.assignments).toEqual({
      reply: "ollama",
    });
    expect(result.current.assignedRoutes.reply).toMatchObject({
      id: "ollama",
      readiness: "ready",
    });
    expect(result.current.assignmentDirty).toBe(false);
  });

  it("normalizes omitted assignments into a complete nullable draft", async () => {
    sdkMocks.getAiRoutes.mockResolvedValue(
      apiResult(catalog({ assignments: {} })),
    );

    const { result } = renderHook(() => useAiRoutes());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.draftAssignments).toEqual({
      reply: null,
    });
    expect(result.current.assignmentDirty).toBe(false);
  });

  it("refreshes the catalog and selected route when its window regains focus", async () => {
    const staleCatalog = catalog({
      routes: [route({ id: "stale", readiness: "setup_required" })],
      assignments: { reply: "stale" },
    });
    const refreshedCatalog = catalog({
      routes: [
        route({ id: "ollama", readiness: "ready", message: "Ollama is ready" }),
      ],
      assignments: { reply: "ollama" },
    });
    sdkMocks.getAiRoutes
      .mockResolvedValueOnce(apiResult(staleCatalog))
      .mockResolvedValueOnce(apiResult(refreshedCatalog));

    const { result } = renderHook(() => useAiRoutes());

    await waitFor(() =>
      expect(result.current.assignedRoutes.reply?.id).toBe("stale"),
    );
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });

    await waitFor(() => expect(sdkMocks.getAiRoutes).toHaveBeenCalledTimes(2));
    expect(result.current.assignments?.reply).toBe("ollama");
    expect(result.current.assignedRoutes.reply).toMatchObject({
      id: "ollama",
      readiness: "ready",
      message: "Ollama is ready",
    });
  });

  it("ignores hidden visibility changes and refreshes when the document becomes visible", async () => {
    const initialCatalog = catalog({
      routes: [route({ id: "before" })],
      assignments: { reply: "before" },
    });
    const visibleCatalog = catalog({
      routes: [route({ id: "after" })],
      assignments: { reply: "after" },
    });
    sdkMocks.getAiRoutes
      .mockResolvedValueOnce(apiResult(initialCatalog))
      .mockResolvedValueOnce(apiResult(visibleCatalog));

    const { result } = renderHook(() => useAiRoutes());

    await waitFor(() =>
      expect(result.current.assignedRoutes.reply?.id).toBe("before"),
    );
    visibilityState = "hidden";
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(sdkMocks.getAiRoutes).toHaveBeenCalledTimes(1);

    visibilityState = "visible";
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    await waitFor(() => expect(sdkMocks.getAiRoutes).toHaveBeenCalledTimes(2));
    expect(result.current.assignedRoutes.reply?.id).toBe("after");
  });

  it("keeps an unsaved assignment draft when the window regains focus", async () => {
    sdkMocks.getAiRoutes.mockResolvedValue(
      apiResult(
        catalog({
          routes: [route({ id: "saved", readiness: "ready" })],
          assignments: { reply: "saved" },
        }),
      ),
    );

    const { result } = renderHook(() => useAiRoutes());

    await waitFor(() =>
      expect(result.current.assignedRoutes.reply?.id).toBe("saved"),
    );
    act(() => {
      result.current.setDraftAssignment("reply", "draft");
    });

    expect(result.current.assignmentDirty).toBe(true);
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });

    expect(sdkMocks.getAiRoutes).toHaveBeenCalledTimes(2);
    expect(result.current.draftAssignments).toEqual({
      reply: "draft",
    });
    expect(result.current.assignedRoutes.reply?.id).toBe("saved");
    expect(result.current.replyStatus).toMatchObject({
      readiness: "ready",
      canGenerate: true,
    });
    act(() => {
      result.current.resetDraftAssignments();
    });
    expect(result.current.draftAssignments).toEqual({
      reply: "saved",
    });
    expect(result.current.assignmentDirty).toBe(false);
  });

  it("removes focus and visibility listeners on unmount", async () => {
    sdkMocks.getAiRoutes.mockResolvedValue(apiResult(catalog()));

    const { unmount } = renderHook(() => useAiRoutes());

    await waitForInitialCatalog();
    unmount();

    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(sdkMocks.getAiRoutes).toHaveBeenCalledTimes(1);
  });

  it("keeps explicit reload available after the initial load", async () => {
    const initialCatalog = catalog({
      routes: [route({ id: "first" })],
      assignments: { reply: "first" },
    });
    const reloadedCatalog = catalog({
      routes: [route({ id: "second" })],
      assignments: { reply: "second" },
    });
    sdkMocks.getAiRoutes
      .mockResolvedValueOnce(apiResult(initialCatalog))
      .mockResolvedValueOnce(apiResult(reloadedCatalog));

    const { result } = renderHook(() => useAiRoutes());

    await waitFor(() =>
      expect(result.current.assignments?.reply).toBe("first"),
    );
    await act(async () => {
      await result.current.reload();
    });

    expect(sdkMocks.getAiRoutes).toHaveBeenCalledTimes(2);
    expect(result.current.assignments?.reply).toBe("second");
    expect(result.current.draftAssignments.reply).toBe("second");
    expect(result.current.assignmentDirty).toBe(false);
    expect(result.current.manualReloadStatus).toBe("success");
    expect(result.current.assignedRoutes.reply?.id).toBe("second");
  });

  it("keeps every dirty assignment while applying fresh routes from a manual reload", async () => {
    const initialCatalog = catalog({
      routes: [
        route({ id: "saved", readiness: "ready" }),
        route({
          id: "draft",
          readiness: "setup_required",
          message: "Set up first",
        }),
      ],
      assignments: { reply: "saved" },
    });
    const refreshedCatalog = catalog({
      routes: [
        route({ id: "saved", readiness: "ready" }),
        route({ id: "draft", readiness: "ready", message: "Ready now" }),
      ],
      assignments: { reply: "saved" },
    });
    sdkMocks.getAiRoutes
      .mockResolvedValueOnce(apiResult(initialCatalog))
      .mockResolvedValueOnce(apiResult(refreshedCatalog));

    const { result } = renderHook(() => useAiRoutes());

    await waitFor(() =>
      expect(result.current.assignedRoutes.reply?.id).toBe("saved"),
    );
    act(() => {
      result.current.setDraftAssignment("reply", "draft");
    });
    await act(async () => {
      await result.current.reload();
    });

    expect(result.current.assignments).toEqual({
      reply: "saved",
    });
    expect(result.current.draftAssignments).toEqual({
      reply: "draft",
    });
    expect(result.current.assignmentDirty).toBe(true);
    expect(
      result.current.routes.find((route) => route.id === "draft"),
    ).toMatchObject({
      readiness: "ready",
      message: "Ready now",
    });
    expect(result.current.replyStatus.canGenerate).toBe(true);
    expect(result.current.manualReloadStatus).toBe("success");
  });

  it("suppresses repeated manual reloads while a manual reload is in progress", async () => {
    let resolveReload!: (value: {
      data: RouteCatalogResponse;
      error: undefined;
    }) => void;
    const pendingReload = new Promise<{
      data: RouteCatalogResponse;
      error: undefined;
    }>((resolve) => {
      resolveReload = resolve;
    });
    sdkMocks.getAiRoutes
      .mockResolvedValueOnce(apiResult(catalog()))
      .mockImplementationOnce(() => pendingReload);

    const { result } = renderHook(() => useAiRoutes());

    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => {
      void result.current.reload();
      void result.current.reload();
    });

    await waitFor(() => expect(sdkMocks.getAiRoutes).toHaveBeenCalledTimes(2));
    expect(result.current.manualReloadStatus).toBe("loading");
    await act(async () => {
      resolveReload(apiResult(catalog()));
      await pendingReload;
    });

    await waitFor(() =>
      expect(result.current.manualReloadStatus).toBe("success"),
    );
  });

  it("keeps the newest catalog when an earlier automatic refresh resolves last", async () => {
    let resolveAutomatic!: (value: {
      data: RouteCatalogResponse;
      error: undefined;
    }) => void;
    let resolveManual!: (value: {
      data: RouteCatalogResponse;
      error: undefined;
    }) => void;
    const automaticRefresh = new Promise<{
      data: RouteCatalogResponse;
      error: undefined;
    }>((resolve) => {
      resolveAutomatic = resolve;
    });
    const manualReload = new Promise<{
      data: RouteCatalogResponse;
      error: undefined;
    }>((resolve) => {
      resolveManual = resolve;
    });
    sdkMocks.getAiRoutes
      .mockResolvedValueOnce(
        apiResult(
          catalog({
            routes: [route({ id: "initial" })],
            assignments: { reply: "initial" },
          }),
        ),
      )
      .mockImplementationOnce(() => automaticRefresh)
      .mockImplementationOnce(() => manualReload);

    const { result } = renderHook(() => useAiRoutes());

    await waitFor(() =>
      expect(result.current.assignedRoutes.reply?.id).toBe("initial"),
    );
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(sdkMocks.getAiRoutes).toHaveBeenCalledTimes(2));
    act(() => {
      void result.current.reload();
    });
    await waitFor(() => expect(sdkMocks.getAiRoutes).toHaveBeenCalledTimes(3));
    await act(async () => {
      resolveManual(
        apiResult(
          catalog({
            routes: [route({ id: "manual" })],
            assignments: { reply: "manual" },
          }),
        ),
      );
      await manualReload;
    });

    expect(result.current.assignedRoutes.reply?.id).toBe("manual");
    await act(async () => {
      resolveAutomatic(
        apiResult(
          catalog({
            routes: [route({ id: "automatic" })],
            assignments: { reply: "automatic" },
          }),
        ),
      );
      await automaticRefresh;
    });

    expect(result.current.assignedRoutes.reply?.id).toBe("manual");
  });

  it("reports a manual reload failure without discarding the existing catalog", async () => {
    const initialCatalog = catalog({
      routes: [route({ id: "saved" })],
      assignments: { reply: "saved" },
    });
    sdkMocks.getAiRoutes
      .mockResolvedValueOnce(apiResult(initialCatalog))
      .mockResolvedValueOnce({ data: undefined, error: new Error("offline") });

    const { result } = renderHook(() => useAiRoutes());

    await waitFor(() =>
      expect(result.current.assignedRoutes.reply?.id).toBe("saved"),
    );
    await act(async () => {
      await result.current.reload();
    });

    expect(result.current.manualReloadStatus).toBe("error");
    expect(result.current.error).toBe(LOAD_ERROR);
    expect(result.current.assignedRoutes.reply?.id).toBe("saved");
  });

  it("saves all changed assignments atomically and adopts the returned catalog", async () => {
    const savedCatalog = catalog({
      routes: [route({ id: "other" })],
      assignments: { reply: "other" },
    });
    sdkMocks.getAiRoutes.mockResolvedValue(apiResult(catalog()));
    sdkMocks.replaceAiRouteAssignments.mockResolvedValue(
      apiResult(savedCatalog),
    );

    const { result } = renderHook(() => useAiRoutes());

    await waitForInitialCatalog();
    act(() => {
      result.current.setDraftAssignment("reply", null);
      result.current.setDraftAssignment("reply", "other");
    });

    let saved = false;
    await act(async () => {
      saved = await result.current.saveAssignments();
    });

    expect(saved).toBe(true);
    expect(sdkMocks.replaceAiRouteAssignments).toHaveBeenCalledWith({
      body: { reply: "other" },
    });
    expect(result.current.assignments).toEqual({
      reply: "other",
    });
    expect(result.current.draftAssignments).toEqual({
      reply: "other",
    });
    expect(result.current.assignmentDirty).toBe(false);
  });

  it("keeps the load and save error contracts", async () => {
    sdkMocks.getAiRoutes.mockResolvedValue({
      data: undefined,
      error: new Error("offline"),
    });

    const { result } = renderHook(() => useAiRoutes());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe(LOAD_ERROR);

    sdkMocks.getAiRoutes.mockResolvedValue(apiResult(catalog()));
    await act(async () => {
      await result.current.reload();
    });
    act(() => {
      result.current.setDraftAssignment("reply", null);
      result.current.setDraftAssignment("reply", "other");
    });
    sdkMocks.replaceAiRouteAssignments.mockResolvedValue({
      data: undefined,
      error: new Error("save failed"),
    });

    let saved = true;
    await act(async () => {
      saved = await result.current.saveAssignments();
    });

    expect(saved).toBe(false);
    expect(result.current.error).toBe(SAVE_ERROR);
    expect(result.current.draftAssignments).toEqual({
      reply: "other",
    });
    expect(result.current.assignmentDirty).toBe(true);
  });
});
