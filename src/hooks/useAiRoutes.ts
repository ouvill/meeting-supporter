import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getAiRoutesApiAiRoutesGet,
  replaceAiRouteAssignmentsApiAiRoutesAssignmentsPut,
} from "../api/generated/sdk.gen";
import type {
  BillingOwner,
  DataLocation,
  RouteAction,
  RouteAssignmentsReadModel,
  RouteAssignmentsUpdate,
  RouteAvailability,
  RouteCapability,
  RouteCatalogResponse,
  RouteKind,
  RouteReadModel,
  RouteReadiness,
} from "../api/generated/types.gen";

export type AiRouteKind = RouteKind;
export type AiRouteAvailability = RouteAvailability;
export type AiRouteReadiness = RouteReadiness;
export type AiRouteDataLocation = DataLocation;
export type AiRouteBillingOwner = BillingOwner;
export type AiRouteCapability = RouteCapability;
export type AiRouteAction = RouteAction;
export type AiRouteReadModel = RouteReadModel;
export type AiRouteAssignments = RouteAssignmentsReadModel;
export type AiRouteCatalog = RouteCatalogResponse;
export type AiAssignableUseCase = "reply" | "info" | "minutes";
export type AiRouteDraftAssignments = Record<
  AiAssignableUseCase,
  string | null
>;

const ASSIGNABLE_USE_CASES: readonly AiAssignableUseCase[] = [
  "reply",
  "info",
  "minutes",
];

function normalizeAssignments(
  assignments: AiRouteAssignments | null | undefined,
): AiRouteDraftAssignments {
  return {
    reply: assignments?.reply ?? null,
    info: assignments?.info ?? null,
    minutes: assignments?.minutes ?? null,
  };
}

function assignmentsAreEqual(
  draft: AiRouteDraftAssignments,
  saved: AiRouteAssignments | null | undefined,
): boolean {
  return ASSIGNABLE_USE_CASES.every(
    (useCase) => draft[useCase] === (saved?.[useCase] ?? null),
  );
}

const LOAD_ERROR =
  "支援方法の状態を確認できませんでした。しばらくしてから再度お試しください。";
const SAVE_ERROR =
  "支援方法を保存できませんでした。選択内容を確認して再度お試しください。";

export type AiRoutesReloadStatus = "idle" | "loading" | "success" | "error";
export interface AiUseCaseRouteStatus {
  readiness: AiRouteReadiness | "unknown";
  canGenerate: boolean;
  message: string | null;
}

interface UseCaseRouteStatusInput {
  loading: boolean;
  error: string | null;
  assignedRouteId: string | null;
  selectedRoute: AiRouteReadModel | null;
}

const USE_CASE_ROUTE_MESSAGES: Record<
  AiAssignableUseCase,
  { unassigned: string; unsupported: string }
> = {
  reply: {
    unassigned: "返答案を利用する支援方法を設定してください。",
    unsupported: "選択した支援方法では返答案を利用できません。",
  },
  info: {
    unassigned: "会話メモを利用する支援方法を設定してください。",
    unsupported: "選択した支援方法では会話メモを利用できません。",
  },
  minutes: {
    unassigned: "議事録を利用する支援方法を設定してください。",
    unsupported: "選択した支援方法では議事録を利用できません。",
  },
};

export function resolveUseCaseRouteStatus(
  capability: AiAssignableUseCase,
  { loading, error, assignedRouteId, selectedRoute }: UseCaseRouteStatusInput,
): AiUseCaseRouteStatus {
  if (loading)
    return { readiness: "unknown", canGenerate: false, message: null };
  if (error)
    return { readiness: "error", canGenerate: false, message: LOAD_ERROR };
  if (!assignedRouteId || !selectedRoute) {
    return {
      readiness: "setup_required",
      canGenerate: false,
      message: USE_CASE_ROUTE_MESSAGES[capability].unassigned,
    };
  }
  if (
    !selectedRoute.capabilities.includes(capability) ||
    (selectedRoute.readiness === "ready" && !selectedRoute.selectable)
  ) {
    return {
      readiness: "unavailable",
      canGenerate: false,
      message: USE_CASE_ROUTE_MESSAGES[capability].unsupported,
    };
  }
  if (selectedRoute.readiness === "ready") {
    return { readiness: "ready", canGenerate: true, message: null };
  }
  return {
    readiness: selectedRoute.readiness,
    canGenerate: false,
    message: selectedRoute.message,
  };
}

type CatalogFetchReason = "initial" | "automatic" | "manual";

export function useAiRoutes() {
  const [catalog, setCatalog] = useState<AiRouteCatalog | null>(null);
  const [draftAssignments, setDraftAssignmentsState] =
    useState<AiRouteDraftAssignments>(() => normalizeAssignments(null));
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manualReloadStatus, setManualReloadStatus] =
    useState<AiRoutesReloadStatus>("idle");
  const catalogRef = useRef<AiRouteCatalog | null>(null);
  const draftAssignmentsRef = useRef<AiRouteDraftAssignments>(
    normalizeAssignments(null),
  );
  const latestLoadRequestRef = useRef(0);
  const automaticRefreshInFlightRef = useRef(false);
  const manualReloadInFlightRef = useRef(false);

  const applyDraftAssignments = useCallback(
    (assignments: AiRouteAssignments | null | undefined) => {
      const next = normalizeAssignments(assignments);
      draftAssignmentsRef.current = next;
      setDraftAssignmentsState(next);
    },
    [],
  );

  const setDraftAssignment = useCallback(
    (useCase: AiAssignableUseCase, routeId: string | null) => {
      if (draftAssignmentsRef.current[useCase] === routeId) {
        return;
      }
      const next = { ...draftAssignmentsRef.current, [useCase]: routeId };
      draftAssignmentsRef.current = next;
      setDraftAssignmentsState(next);
    },
    [],
  );

  const fetchCatalog = useCallback(
    async (reason: CatalogFetchReason) => {
      const assignmentIsDirty = !assignmentsAreEqual(
        draftAssignmentsRef.current,
        catalogRef.current?.assignments,
      );
      if (
        (reason === "automatic" &&
          (assignmentIsDirty || manualReloadInFlightRef.current)) ||
        (reason === "manual" && manualReloadInFlightRef.current)
      ) {
        return;
      }

      const preserveDraft = reason === "manual" && assignmentIsDirty;
      const requestId = ++latestLoadRequestRef.current;
      if (reason === "manual") {
        manualReloadInFlightRef.current = true;
        setManualReloadStatus("loading");
      }
      setLoading(true);
      setError(null);
      try {
        const result = await getAiRoutesApiAiRoutesGet();
        if (requestId !== latestLoadRequestRef.current) {
          return;
        }
        if (result.error || !result.data) {
          setError(LOAD_ERROR);
          if (reason === "manual") {
            setManualReloadStatus("error");
          }
          return;
        }

        const assignmentBecameDirty = !assignmentsAreEqual(
          draftAssignmentsRef.current,
          catalogRef.current?.assignments,
        );
        if (reason === "automatic" && assignmentBecameDirty) {
          return;
        }

        catalogRef.current = result.data;
        setCatalog(result.data);
        if (!(reason === "manual" && (preserveDraft || assignmentBecameDirty))) {
          applyDraftAssignments(result.data.assignments);
        }
        if (reason === "manual") {
          setManualReloadStatus("success");
        }
      } catch {
        if (requestId === latestLoadRequestRef.current) {
          setError(LOAD_ERROR);
          if (reason === "manual") {
            setManualReloadStatus("error");
          }
        }
      } finally {
        if (requestId === latestLoadRequestRef.current) {
          setLoading(false);
        }
        if (reason === "manual") {
          manualReloadInFlightRef.current = false;
        }
      }
    },
    [applyDraftAssignments],
  );

  const reload = useCallback(() => fetchCatalog("manual"), [fetchCatalog]);

  useEffect(() => {
    void fetchCatalog("initial");
  }, [fetchCatalog]);

  useEffect(() => {
    const refreshIfAssignmentClean = () => {
      if (
        document.visibilityState === "hidden" ||
        !assignmentsAreEqual(
          draftAssignmentsRef.current,
          catalogRef.current?.assignments,
        ) ||
        automaticRefreshInFlightRef.current ||
        manualReloadInFlightRef.current
      ) {
        return;
      }

      automaticRefreshInFlightRef.current = true;
      void fetchCatalog("automatic").finally(() => {
        automaticRefreshInFlightRef.current = false;
      });
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        refreshIfAssignmentClean();
      }
    };

    window.addEventListener("focus", refreshIfAssignmentClean);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.removeEventListener("focus", refreshIfAssignmentClean);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [fetchCatalog]);

  const saveAssignments = useCallback(async () => {
    if (!catalog) {
      setError(SAVE_ERROR);
      return false;
    }
    if (assignmentsAreEqual(draftAssignments, catalog.assignments)) {
      return true;
    }

    setSaving(true);
    const assignments: RouteAssignmentsUpdate = { ...draftAssignments };

    try {
      const result = await replaceAiRouteAssignmentsApiAiRoutesAssignmentsPut({
        body: assignments,
      });
      if (result.error || !result.data) {
        setError(SAVE_ERROR);
        return false;
      }
      catalogRef.current = result.data;
      setCatalog(result.data);
      applyDraftAssignments(result.data.assignments);
      return true;
    } catch {
      setError(SAVE_ERROR);
      return false;
    } finally {
      setSaving(false);
    }
  }, [applyDraftAssignments, catalog, draftAssignments]);

  const assignmentDirty =
    catalog !== null && !assignmentsAreEqual(draftAssignments, catalog.assignments);

  const assignedRoutes = useMemo<Record<
    AiAssignableUseCase,
    AiRouteReadModel | null
  >>(() => {
    const findAssignedRoute = (useCase: AiAssignableUseCase) =>
      catalog?.routes.find(
        (route) => route.id === (catalog.assignments[useCase] ?? null),
      ) ?? null;
    return {
      reply: findAssignedRoute("reply"),
      info: findAssignedRoute("info"),
      minutes: findAssignedRoute("minutes"),
    };
  }, [catalog]);
  const routeStatuses = useMemo(
    () => ({
      reply: resolveUseCaseRouteStatus("reply", {
        loading,
        error,
        assignedRouteId: catalog?.assignments.reply ?? null,
        selectedRoute: assignedRoutes.reply,
      }),
      info: resolveUseCaseRouteStatus("info", {
        loading,
        error,
        assignedRouteId: catalog?.assignments.info ?? null,
        selectedRoute: assignedRoutes.info,
      }),
      minutes: resolveUseCaseRouteStatus("minutes", {
        loading,
        error,
        assignedRouteId: catalog?.assignments.minutes ?? null,
        selectedRoute: assignedRoutes.minutes,
      }),
    }),
    [assignedRoutes, catalog?.assignments, error, loading],
  );

  const resetDraftAssignments = useCallback(() => {
    applyDraftAssignments(catalog?.assignments);
  }, [applyDraftAssignments, catalog?.assignments]);

  return {
    routes: catalog?.routes ?? [],
    assignments: catalog?.assignments ?? null,
    assignedRoutes,
    replyStatus: routeStatuses.reply,
    infoRouteStatus: routeStatuses.info,
    minutesRouteStatus: routeStatuses.minutes,
    draftAssignments,
    assignmentDirty,
    setDraftAssignment,
    resetDraftAssignments,
    loading,
    saving,
    error,
    manualReloadStatus,
    reload,
    saveAssignments,
  };
}

export type AiRoutesController = ReturnType<typeof useAiRoutes>;
