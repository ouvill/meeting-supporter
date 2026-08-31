import { useCallback, useEffect, useRef, useState } from "react";
import {
  getManagedAuthStatus,
  getManagedEntitlement,
  onManagedAuthChanged,
} from "../platform/managedServiceClient";

export interface ManagedSttAvailability {
  offered: boolean;
  loading: boolean;
  authenticated: boolean;
  selectable: boolean;
  message: string;
  refresh: () => Promise<void>;
}

export function useManagedSttAvailability(
  offered: boolean,
  enabled: boolean,
  onAuthChanged?: () => void,
): ManagedSttAvailability {
  const [loading, setLoading] = useState(offered);
  const [authenticated, setAuthenticated] = useState(false);
  const [selectable, setSelectable] = useState(false);
  const [message, setMessage] = useState(
    offered ? "利用状態を確認しています。" : "このビルドでは提供していません。",
  );
  const onAuthChangedRef = useRef(onAuthChanged);
  const requestGenerationRef = useRef(0);

  useEffect(() => {
    onAuthChangedRef.current = onAuthChanged;
  }, [onAuthChanged]);

  const runRefresh = useCallback(async (): Promise<boolean> => {
    const generation = ++requestGenerationRef.current;
    const isCurrent = () => requestGenerationRef.current === generation;

    if (!offered) {
      if (!isCurrent()) return false;
      setLoading(false);
      if (!isCurrent()) return false;
      setAuthenticated(false);
      if (!isCurrent()) return false;
      setSelectable(false);
      if (!isCurrent()) return false;
      setMessage("このビルドでは提供していません。");
      return true;
    }

    if (!isCurrent()) return false;
    setLoading(true);
    try {
      const auth = await getManagedAuthStatus();
      if (!isCurrent()) return false;
      setAuthenticated(auth.authenticated);
      if (!auth.authenticated) {
        if (!isCurrent()) return false;
        setSelectable(false);
        if (!isCurrent()) return false;
        setMessage("アカウント設定からログインしてください。");
        return true;
      }

      const entitlement = await getManagedEntitlement();
      if (!isCurrent()) return false;
      const isSelectable =
        entitlement.managed.readiness === "ready" &&
        entitlement.managed.speech_recognition.selectable === true;
      setSelectable(isSelectable);
      if (!isCurrent()) return false;
      if (isSelectable) setMessage("月額プランの共通利用枠で利用できます。");
      else if (entitlement.managed.readiness === "subscription_required")
        setMessage("月額プランの契約が必要です。");
      else if (entitlement.managed.readiness === "payment_required")
        setMessage("支払い方法を確認してください。");
      else if (entitlement.managed.readiness === "quota_exhausted")
        setMessage("今月の共通利用枠を使い切りました。");
      else setMessage("現在、Meeting Supporter 音声認識を利用できません。");
    } catch {
      if (!isCurrent()) return false;
      setAuthenticated(false);
      if (!isCurrent()) return false;
      setSelectable(false);
      if (!isCurrent()) return false;
      setMessage("利用状態を確認できませんでした。");
    } finally {
      if (isCurrent()) setLoading(false);
    }
    return isCurrent();
  }, [offered]);

  const refresh = useCallback(async () => {
    await runRefresh();
  }, [runRefresh]);

  useEffect(() => {
    if (!enabled) {
      requestGenerationRef.current += 1;
      return;
    }

    void runRefresh();
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void onManagedAuthChanged(() => {
      if (disposed) return;
      void runRefresh().then((current) => {
        if (!disposed && current) onAuthChangedRef.current?.();
      });
    }).then((cleanup) => {
      if (disposed) cleanup();
      else unlisten = cleanup;
    });
    return () => {
      disposed = true;
      requestGenerationRef.current += 1;
      unlisten?.();
    };
  }, [enabled, runRefresh]);

  return { offered, loading, authenticated, selectable, message, refresh };
}
