/**
 * Validated boundary for the desktop backend bootstrap IPC command.
 */
import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";

const BackendCrashInfoSchema = z
  .object({
    unexpected: z.boolean(),
    exit_code: z.number().int().nullable(),
    signal: z.number().int().nullable(),
    message: z.string(),
  })
  .strict();

const BackendBootstrapSnapshotSchema = z
  .object({
    phase: z.string(),
    message: z.string(),
    running: z.boolean(),
    port: z.number().int().min(1).max(65_535).nullable(),
    auth_token: z.string().min(1).nullable(),
    crash: BackendCrashInfoSchema.nullable(),
  })
  .strict();

export type BackendCrashInfo = z.infer<typeof BackendCrashInfoSchema>;
export type BackendBootstrapSnapshot = z.infer<
  typeof BackendBootstrapSnapshotSchema
>;
export type BootstrapStatus = Pick<
  BackendBootstrapSnapshot,
  "phase" | "message"
>;

const FALLBACK_BOOTSTRAP_SNAPSHOT: BackendBootstrapSnapshot = {
  phase: "initializing",
  message: "Pythonバックエンドを起動しています...",
  running: false,
  port: null,
  auth_token: null,
  crash: null,
};

let pendingBootstrapSnapshot: Promise<BackendBootstrapSnapshot> | null = null;

/**
 * Obtains and validates one coherent point-in-time view of backend startup.
 * IPC failures propagate so the polling hook can classify and report them.
 */
export function getBackendBootstrapSnapshot(): Promise<BackendBootstrapSnapshot> {
  if (pendingBootstrapSnapshot !== null) return pendingBootstrapSnapshot;

  const request = invoke<unknown>("get_backend_bootstrap_snapshot").then((raw) => {
    const parsed = BackendBootstrapSnapshotSchema.safeParse(raw);
    if (!parsed.success) {
      console.warn(
        "[BootstrapClient] Invalid bootstrap snapshot shape:",
        parsed.error,
      );
      return FALLBACK_BOOTSTRAP_SNAPSHOT;
    }
    return parsed.data;
  });
  pendingBootstrapSnapshot = request;
  void request.then(
    () => {
      if (pendingBootstrapSnapshot === request) pendingBootstrapSnapshot = null;
    },
    () => {
      if (pendingBootstrapSnapshot === request) pendingBootstrapSnapshot = null;
    },
  );
  return request;
}

/**
 * Expected transient bootstrap failures are suppressed by the polling hook.
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
