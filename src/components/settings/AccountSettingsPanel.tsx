import {
  CalendarClock,
  CreditCard,
  Gauge,
  LoaderCircle,
  LogIn,
  LogOut,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  deleteManagedAccount,
  getManagedAuthStatus,
  getManagedEntitlement,
  logoutManagedAuth,
  openManagedBillingPortal,
  onManagedAuthChanged,
  openManagedCheckout,
  startManagedAuth,
  type ManagedAuthStatus,
  type ManagedEntitlement,
} from "../../platform/managedServiceClient";
import { Button } from "../ui/Button";
import { InlineNotice } from "../ui/InlineNotice";
import { SettingsCard, SettingsPage } from "./SettingsPrimitives";

interface Props {
  offered: boolean;
  managedActionsLocked?: boolean;
  onChanged: () => void;
}

function planLabel(status: string): string {
  if (status === "active" || status === "trialing") return "月額プラン利用中";
  if (status === "past_due") return "支払いの確認が必要";
  return "未契約";
}

function renewalLabel(timestamp: number | null): string {
  if (timestamp === null) return "契約後に表示します";
  return new Intl.DateTimeFormat("ja-JP", {
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(new Date(timestamp * 1000));
}

function quotaStatusLabel(entitlement: ManagedEntitlement): string {
  switch (entitlement.managed.readiness) {
    case "ready":
      return "今月は利用できます";
    case "quota_exhausted":
      return "今月分を使い切りました";
    case "subscription_required":
      return "プラン開始後に利用できます";
    case "payment_required":
      return "支払い確認後に利用できます";
    default:
      return "現在は利用できません";
  }
}

export function AccountSettingsPanel({
  offered,
  managedActionsLocked = false,
  onChanged,
}: Props) {
  const [auth, setAuth] = useState<ManagedAuthStatus | null>(null);
  const [entitlement, setEntitlement] = useState<ManagedEntitlement | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDeletion, setConfirmingDeletion] = useState(false);

  const refresh = useCallback(async () => {
    if (!offered) {
      setAuth(null);
      setEntitlement(null);
      setError(null);
      setLoading(false);
      return;
    }
    setError(null);
    try {
      const nextAuth = await getManagedAuthStatus();
      setAuth(nextAuth);
      setEntitlement(
        nextAuth.authenticated ? await getManagedEntitlement() : null,
      );
    } catch {
      setAuth(null);
      setEntitlement(null);
      setError("アカウント情報を確認できませんでした。");
    } finally {
      setLoading(false);
    }
  }, [offered]);

  useEffect(() => {
    void refresh();
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void onManagedAuthChanged(() => {
      if (!disposed) {
        void refresh().then(onChanged);
      }
    }).then((cleanup) => {
      if (disposed) cleanup();
      else unlisten = cleanup;
    });
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, [onChanged, refresh]);

  const perform = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await refresh();
      onChanged();
    } catch {
      setError(
        "操作を完了できませんでした。状態を再確認してからお試しください。",
      );
    } finally {
      setBusy(false);
    }
  };

  const subscribed =
    entitlement?.plan.status === "active" ||
    entitlement?.plan.status === "trialing";

  return (
    <SettingsPage
      title="アカウントとプラン"
      description="ログイン、月額プラン、共通利用枠をここで管理します。"
    >
      {!offered ? (
        <InlineNotice tone="info">
          このビルドではMeeting Supporter
          AIのアカウントと月額プランを提供していません。
        </InlineNotice>
      ) : (
        <>
          {error && <InlineNotice tone="danger">{error}</InlineNotice>}
          <SettingsCard title="Meeting Supporter アカウント">
            {loading ? (
              <div
                className="flex items-center gap-2 py-6 text-sm text-ink-muted"
                role="status"
              >
                <LoaderCircle
                  className="h-4 w-4 animate-spin"
                  aria-hidden="true"
                />
                アカウント情報を確認しています
              </div>
            ) : auth?.authenticated ? (
              <div className="space-y-4">
                <div className="flex items-center gap-3 rounded-xl border border-positive/20 bg-positive-soft p-4">
                  <span className="flex h-10 w-10 items-center justify-center rounded-full bg-positive text-white">
                    <ShieldCheck className="h-5 w-5" aria-hidden="true" />
                  </span>
                  <div>
                    <p className="font-display text-sm font-bold text-ink">
                      ログイン済み
                    </p>
                    <p className="text-xs text-ink-muted">
                      認証情報はOSの安全な保管領域に保存されます。
                    </p>
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="quiet"
                  disabled={busy || managedActionsLocked}
                  onClick={() => void perform(logoutManagedAuth)}
                >
                  <LogOut className="h-4 w-4" aria-hidden="true" />
                  ログアウト
                </Button>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="rounded-xl border border-line bg-surface p-4">
                  <div className="flex items-center gap-2 font-display text-sm font-bold text-ink">
                    <Sparkles
                      className="h-4 w-4 text-primary"
                      aria-hidden="true"
                    />
                    Google または Microsoft でログイン
                  </div>
                  <p className="mt-2 text-sm leading-relaxed text-ink-muted">
                    Meeting Supporter
                    AIの契約と利用枠を、このPCで安全に利用できるようにします。
                  </p>
                </div>
                <Button
                  size="md"
                  variant="primary"
                  disabled={busy || managedActionsLocked}
                  onClick={() => void perform(startManagedAuth)}
                >
                  <LogIn className="h-4 w-4" aria-hidden="true" />
                  ブラウザでログイン
                </Button>
              </div>
            )}
          </SettingsCard>

          {auth?.authenticated && entitlement && (
            <>
              <SettingsCard title="月額プラン">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-xl border border-line bg-surface p-4">
                    <div className="flex items-center gap-2 text-xs font-semibold text-ink-muted">
                      <CreditCard className="h-4 w-4" aria-hidden="true" />
                      契約状態
                    </div>
                    <p className="mt-2 font-display text-lg font-bold text-ink">
                      {planLabel(entitlement.plan.status)}
                    </p>
                    <p className="mt-1 text-xs text-ink-muted">
                      月額3,000円（税込）
                    </p>
                  </div>
                  <div className="rounded-xl border border-line bg-surface p-4">
                    <div className="flex items-center gap-2 text-xs font-semibold text-ink-muted">
                      <CalendarClock className="h-4 w-4" aria-hidden="true" />
                      利用枠の更新日
                    </div>
                    <p className="mt-2 font-display text-base font-bold text-ink">
                      {renewalLabel(entitlement.quota.renews_at)}
                    </p>
                    <p className="mt-1 text-xs text-ink-muted">
                      繰り越し・超過課金なし
                    </p>
                  </div>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  {!subscribed ? (
                    <Button
                      size="md"
                      variant="primary"
                      disabled={busy || managedActionsLocked}
                      onClick={() => void perform(openManagedCheckout)}
                    >
                      月額プランを申し込む
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={busy || managedActionsLocked}
                      onClick={() => void perform(openManagedBillingPortal)}
                    >
                      支払い・解約を管理
                    </Button>
                  )}
                </div>
              </SettingsCard>

              <SettingsCard title="プランに含まれるAI利用枠">
                <div className="flex items-end justify-between gap-4 rounded-xl border border-primary/15 bg-primary-soft p-4">
                  <div>
                    <div className="flex items-center gap-2 text-xs font-semibold text-primary">
                      <Gauge className="h-4 w-4" aria-hidden="true" />
                      今月の利用状況
                    </div>
                    <p className="mt-2 font-display text-2xl font-bold text-ink">
                      {quotaStatusLabel(entitlement)}
                    </p>
                  </div>
                  <span className="rounded-full bg-paper px-2.5 py-1 text-xs font-semibold text-ink-muted">
                    返答案・音声認識で共通
                  </span>
                </div>
                <p className="mt-3 text-xs leading-relaxed text-ink-muted">
                  返答案とクラウド音声認識を月額内で利用できます。利用枠は毎月リセットされ、上限に達すると自動で停止します。追加料金は発生しません。
                </p>
              </SettingsCard>

              <SettingsCard title="アカウントの削除">
                {!confirmingDeletion ? (
                  <Button
                    size="sm"
                    variant="quiet"
                    disabled={busy || managedActionsLocked}
                    onClick={() => setConfirmingDeletion(true)}
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                    アカウントを削除
                  </Button>
                ) : (
                  <InlineNotice tone="danger">
                    <p>
                      契約を停止し、利用履歴と認証情報を削除します。この操作は取り消せません。
                    </p>
                    <div className="mt-3 flex gap-2">
                      <Button
                        size="sm"
                        variant="danger"
                        disabled={busy || managedActionsLocked}
                        onClick={() => void perform(deleteManagedAccount)}
                      >
                        削除を実行
                      </Button>
                      <Button
                        size="sm"
                        variant="quiet"
                        disabled={busy}
                        onClick={() => setConfirmingDeletion(false)}
                      >
                        キャンセル
                      </Button>
                    </div>
                  </InlineNotice>
                )}
              </SettingsCard>
            </>
          )}
        </>
      )}
    </SettingsPage>
  );
}
