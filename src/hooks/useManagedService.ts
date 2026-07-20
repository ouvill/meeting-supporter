import { useCallback, useEffect, useState } from "react";
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
  onAuthChanged?: () => void,
): ManagedSttAvailability {
  const [loading, setLoading] = useState(offered);
  const [authenticated, setAuthenticated] = useState(false);
  const [selectable, setSelectable] = useState(false);
  const [message, setMessage] = useState(
    offered ? "利用状態を確認しています。" : "このビルドでは提供していません。",
  );

  const refresh = useCallback(async () => {
    if (!offered) {
      setLoading(false);
      setAuthenticated(false);
      setSelectable(false);
      setMessage("このビルドでは提供していません。");
      return;
    }
    setLoading(true);
    try {
      const auth = await getManagedAuthStatus();
      setAuthenticated(auth.authenticated);
      if (!auth.authenticated) {
        setSelectable(false);
        setMessage("アカウント設定からログインしてください。");
        return;
      }
      const entitlement = await getManagedEntitlement();
      const enabled =
        entitlement.managed.readiness === "ready" &&
        entitlement.managed.speech_recognition.selectable === true;
      setSelectable(enabled);
      if (enabled) setMessage("月額プランの共通利用枠で利用できます。");
      else if (entitlement.managed.readiness === "subscription_required")
        setMessage("月額プランの契約が必要です。");
      else if (entitlement.managed.readiness === "payment_required")
        setMessage("支払い方法を確認してください。");
      else if (entitlement.managed.readiness === "quota_exhausted")
        setMessage("今月の共通利用枠を使い切りました。");
      else setMessage("現在、Meeting Supporter 音声認識を利用できません。");
    } catch {
      setAuthenticated(false);
      setSelectable(false);
      setMessage("利用状態を確認できませんでした。");
    } finally {
      setLoading(false);
    }
  }, [offered]);

  useEffect(() => {
    void refresh();
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void onManagedAuthChanged(() => {
      if (!disposed) void refresh().then(onAuthChanged);
    }).then((cleanup) => {
      if (disposed) cleanup();
      else unlisten = cleanup;
    });
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, [onAuthChanged, refresh]);

  return { offered, loading, authenticated, selectable, message, refresh };
}
