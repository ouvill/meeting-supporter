import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { z } from "zod";

const ManagedAuthStatusSchema = z.object({
  authenticated: z.boolean(),
  reason: z.string(),
});

const CapabilitySchema = z.object({
  enabled: z.boolean(),
  selectable: z.boolean(),
});

const ManagedEntitlementSchema = z.object({
  account: z.object({ status: z.string() }),
  plan: z.object({
    status: z.string(),
    cancel_at_period_end: z.boolean(),
  }),
  quota: z.object({
    remaining_micro_usd: z.number(),
    approximate_remaining_jpy: z.number(),
    renews_at: z.number().nullable(),
    shared: z.boolean(),
    rollover: z.boolean(),
    overage_charging: z.boolean(),
  }),
  managed: z.object({
    availability: z.string(),
    readiness: z.string(),
    reason: z.string(),
    action: z.string().nullable(),
    reply: CapabilitySchema,
    speech_recognition: CapabilitySchema,
  }),
});

export type ManagedAuthStatus = z.infer<typeof ManagedAuthStatusSchema>;
export type ManagedEntitlement = z.infer<typeof ManagedEntitlementSchema>;

async function validatedInvoke<T>(
  command: string,
  schema: z.ZodType<T>,
): Promise<T> {
  const result = schema.safeParse(await invoke<unknown>(command));
  if (!result.success) throw new Error(`Invalid response from ${command}`);
  return result.data;
}

export function getManagedAuthStatus(): Promise<ManagedAuthStatus> {
  return validatedInvoke("managed_auth_status", ManagedAuthStatusSchema);
}

export function startManagedAuth(): Promise<ManagedAuthStatus> {
  return validatedInvoke("managed_auth_start", ManagedAuthStatusSchema);
}

export function logoutManagedAuth(): Promise<ManagedAuthStatus> {
  return validatedInvoke("managed_auth_logout", ManagedAuthStatusSchema);
}

export function getManagedEntitlement(): Promise<ManagedEntitlement> {
  return validatedInvoke("managed_entitlement", ManagedEntitlementSchema);
}

export async function openManagedCheckout(): Promise<void> {
  await invoke("managed_checkout");
}

export async function openManagedBillingPortal(): Promise<void> {
  await invoke("managed_billing_portal");
}

export function deleteManagedAccount(): Promise<ManagedAuthStatus> {
  return validatedInvoke("managed_delete_account", ManagedAuthStatusSchema);
}

export async function onManagedAuthChanged(
  listener: () => void,
): Promise<() => void> {
  if (typeof window === "undefined" || !isTauri()) return () => undefined;
  return listen("managed-auth-changed", listener);
}
