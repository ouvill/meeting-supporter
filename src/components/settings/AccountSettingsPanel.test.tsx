import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AccountSettingsPanel } from "./AccountSettingsPanel";

const managed = vi.hoisted(() => ({
  authStatus: vi.fn(),
  entitlement: vi.fn(),
  deleteAccount: vi.fn(),
  logout: vi.fn(),
  billing: vi.fn(),
  authStart: vi.fn(),
}));

vi.mock("../../platform/managedServiceClient", () => ({
  deleteManagedAccount: managed.deleteAccount,
  getManagedAuthStatus: managed.authStatus,
  getManagedEntitlement: managed.entitlement,
  logoutManagedAuth: managed.logout,
  onManagedAuthChanged: vi.fn(async () => () => undefined),
  openManagedBillingPortal: managed.billing,
  openManagedCheckout: vi.fn(),
  startManagedAuth: managed.authStart,
}));

const entitlement = {
  account: { status: "active" },
  plan: { status: "active", cancel_at_period_end: false },
  quota: {
    remaining_micro_usd: 12_000_000,
    approximate_remaining_jpy: 1840,
    renews_at: 1_800_000_000,
    shared: true,
    rollover: false,
    overage_charging: false,
  },
  managed: {
    availability: "experimental",
    readiness: "ready",
    reason: "READY",
    action: null,
    reply: { enabled: true, selectable: true },
    speech_recognition: { enabled: true, selectable: true },
  },
};

describe("AccountSettingsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    managed.authStatus.mockResolvedValue({
      authenticated: true,
      reason: "ready",
    });
    managed.entitlement.mockResolvedValue(entitlement);
  });
  it("presents AI usage as included in the plan without an API yen comparison", async () => {
    const onChanged = vi.fn();
    render(<AccountSettingsPanel offered onChanged={onChanged} />);

    expect(await screen.findByText("月額プラン利用中")).toBeInTheDocument();
    expect(screen.getByText("月額3,000円（税込）")).toBeInTheDocument();
    expect(screen.getByText("今月は利用できます")).toBeInTheDocument();
    expect(screen.getByText("返答案・音声認識で共通")).toBeInTheDocument();
    expect(screen.getByText(/月額内で利用できます/)).toBeInTheDocument();
    expect(screen.queryByText(/円相当/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "支払い・解約を管理" }));
    await waitFor(() => {
      expect(managed.billing).toHaveBeenCalledOnce();
      expect(onChanged).toHaveBeenCalledOnce();
      expect(
        screen.getByRole("button", { name: "支払い・解約を管理" }),
      ).toBeEnabled();
    });
  });

  it("keeps managed status visible while locking account-destructive actions", async () => {
    const onChanged = vi.fn();
    const { rerender } = render(
      <AccountSettingsPanel offered onChanged={onChanged} />,
    );

    expect(await screen.findByText("月額プラン利用中")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "アカウントを削除" }),
    );

    rerender(
      <AccountSettingsPanel
        offered
        managedActionsLocked
        onChanged={onChanged}
      />,
    );

    expect(screen.getByText("今月は利用できます")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ログアウト" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "支払い・解約を管理" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "削除を実行" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "キャンセル" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "ログアウト" }));
    fireEvent.click(
      screen.getByRole("button", { name: "支払い・解約を管理" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "削除を実行" }));
    fireEvent.click(screen.getByRole("button", { name: "キャンセル" }));
    expect(
      screen.getByRole("button", { name: "アカウントを削除" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "アカウントを削除" }),
    );

    expect(managed.logout).not.toHaveBeenCalled();
    expect(managed.billing).not.toHaveBeenCalled();
    expect(managed.deleteAccount).not.toHaveBeenCalled();
  });

  it("does not start a replacement managed sign-in while actions are locked", async () => {
    managed.authStatus.mockResolvedValue({
      authenticated: false,
      reason: "signed_out",
    });
    render(
      <AccountSettingsPanel
        offered
        managedActionsLocked
        onChanged={vi.fn()}
      />,
    );

    const signIn = await screen.findByRole("button", {
      name: "ブラウザでログイン",
    });
    expect(signIn).toBeDisabled();
    fireEvent.click(signIn);
    expect(managed.authStart).not.toHaveBeenCalled();
  });
});
