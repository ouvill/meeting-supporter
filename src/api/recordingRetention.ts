import { z } from "zod";
import { client } from "./generated/client.gen";

export interface RecordingCleanupRequest {
  cutoff_date?: string | null;
  max_total_bytes?: number | null;
}

export interface RecordingCleanupPreview {
  candidate_meeting_ids: string[];
  delete_count: number;
  delete_recording_bytes: number;
  total_recording_bytes_before: number;
  total_recording_bytes_after: number;
}

export interface RecordingCleanupExecution extends RecordingCleanupPreview {
  deleted_meeting_ids: string[];
  failed_meeting_ids: string[];
  skipped_meeting_ids: string[];
}

const previewSchema = z.object({
  candidate_meeting_ids: z.array(z.string()),
  delete_count: z.number().int().nonnegative(),
  delete_recording_bytes: z.number().int().nonnegative(),
  total_recording_bytes_before: z.number().int().nonnegative(),
  total_recording_bytes_after: z.number().int().nonnegative(),
});
const executionSchema = previewSchema.extend({
  deleted_meeting_ids: z.array(z.string()),
  failed_meeting_ids: z.array(z.string()),
  skipped_meeting_ids: z.array(z.string()),
});

function apiHeaders(): Headers {
  const headers = new Headers({ "Content-Type": "application/json" });
  const configured = client.getConfig().headers;
  if (configured) {
    new Headers(configured as HeadersInit).forEach((value, key) =>
      headers.set(key, value),
    );
  }
  return headers;
}

async function postJson<T>(
  path: string,
  body: RecordingCleanupRequest,
  schema: z.ZodType<T>,
): Promise<T> {
  const baseUrl = client.getConfig().baseUrl ?? "";
  const response = await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Recording cleanup request failed: ${response.status}`);
  }
  return schema.parse(await response.json());
}

export function previewRecordingCleanup(
  body: RecordingCleanupRequest,
): Promise<RecordingCleanupPreview> {
  return postJson("/meetings/recordings/cleanup/preview", body, previewSchema);
}

export function executeRecordingCleanup(
  body: RecordingCleanupRequest,
): Promise<RecordingCleanupExecution> {
  return postJson("/meetings/recordings/cleanup", body, executionSchema);
}
