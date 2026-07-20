/**
 * Backend bootstrap IPC client.
 *
 * This module is the sole owner of direct `@tauri-apps/api/core` imports
 * related to backend bootstrap. It wraps `invoke()` with runtime validation
 * and safe defaults so callers never need to deal with raw Tauri IPC types.
 *
 * IPC errors and shape mismatches are handled defensively:
 * - Validation failures log a warning and return a sensible fallback.
 * - Unexpected IPC errors are left to propagate; see `isExpectedBootstrapError`
 *   for the hook-level suppression helper.
 */

import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";

// ---------------------------------------------------------------------------
// Schema & types
// ---------------------------------------------------------------------------

const BootstrapStatusSchema = z.object({
  phase: z.string(),
  message: z.string(),
});

export type BootstrapStatus = z.infer<typeof BootstrapStatusSchema>;

const BackendCrashInfoSchema = z.object({
  unexpected: z.boolean(),
  exit_code: z.number().nullable(),
  signal: z.number().nullable(),
  message: z.string(),
});

export type BackendCrashInfo = z.infer<typeof BackendCrashInfoSchema>;

// ---------------------------------------------------------------------------
// Fallback
// ---------------------------------------------------------------------------

const FALLBACK_BOOTSTRAP_STATUS: BootstrapStatus = {
  phase: "initializing",
  message: "Pythonバックエンドを起動しています...",
};

// ---------------------------------------------------------------------------
// IPC wrappers
// ---------------------------------------------------------------------------

/**
 * Fetch the current backend bootstrap phase & message.
 *
 * Returns validated data or a safe fallback on shape mismatch.
 * Raw IPC errors (network / command-not-found) are propagated to the caller.
 */
export async function getBackendBootstrapStatus(): Promise<BootstrapStatus> {
  const raw = await invoke<unknown>("get_backend_bootstrap_status");
  const parsed = BootstrapStatusSchema.safeParse(raw);
  if (!parsed.success) {
    console.warn(
      "[BootstrapClient] Invalid bootstrap status shape:",
      parsed.error,
    );
    return FALLBACK_BOOTSTRAP_STATUS;
  }
  return parsed.data;
}

/**
 * Check whether the Python backend process is confirmed running.
 */
export async function isBackendRunning(): Promise<boolean> {
  const raw = await invoke<unknown>("is_backend_running");
  if (typeof raw !== "boolean") {
    console.warn(
      "[BootstrapClient] is_backend_running returned non-boolean:",
      raw,
    );
    return false;
  }
  return raw;
}

/**
 * Retrieve the API port the backend is listening on.
 * Returns `null` when the port is not yet available.
 */
export async function getApiPort(): Promise<number | null> {
  const raw = await invoke<unknown>("get_api_port");
  if (typeof raw === "number") return raw;
  if (raw === null) return null;
  console.warn(
    "[BootstrapClient] get_api_port returned unexpected type:",
    typeof raw,
  );
  return null;
}

/**
 * Retrieve the capability token for the local backend.
 * Returns `null` when the backend is not yet available.
 */
export async function getApiAuthToken(): Promise<string | null> {
  const raw = await invoke<unknown>("get_api_auth_token");
  if (typeof raw === "string") return raw;
  if (raw === null) return null;
  console.warn(
    "[BootstrapClient] get_api_auth_token returned unexpected type:",
    typeof raw,
  );
  return null;
}

/**
 * Retrieve the latest backend crash diagnostic info.
 * Returns `null` when no crash has been detected.
 */
export async function getBackendCrashInfo(): Promise<BackendCrashInfo | null> {
  const raw = await invoke<unknown>("get_backend_crash_info");
  if (raw === null) return null;
  const parsed = BackendCrashInfoSchema.safeParse(raw);
  if (!parsed.success) {
    console.warn("[BootstrapClient] Invalid crash info shape:", parsed.error);
    return null;
  }
  return parsed.data;
}

// ---------------------------------------------------------------------------
// Error classification helper (shared with hook)
// ---------------------------------------------------------------------------

/**
 * Returns `true` for errors that are expected during early bootstrap
 * (e.g. "Command not found", "Backend not ready yet").
 *
 * These should be silently ignored rather than logged as warnings.
 *
 * The match is intentionally narrow: only well-known bootstrap-phase
 * messages are suppressed. Arbitrary errors that happen to contain
 * the word "backend" (e.g. "Backend serialization error") must NOT
 * be silenced so they surface in the developer console.
 */
export function isExpectedBootstrapError(err: unknown): boolean {
  const msg =
    typeof err === "string"
      ? err
      : err instanceof Error
        ? err.message
        : String(err);
  const lower = msg.toLowerCase();
  if (lower.includes("not found")) return true;
  return (
    lower.includes("backend not ready") ||
    lower.includes("backend not available") ||
    lower.includes("backend not started")
  );
}
