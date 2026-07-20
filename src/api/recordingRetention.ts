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
  return (await response.json()) as T;
}

export function previewRecordingCleanup(
  body: RecordingCleanupRequest,
): Promise<RecordingCleanupPreview> {
  return postJson("/meetings/recordings/cleanup/preview", body);
}

export function executeRecordingCleanup(
  body: RecordingCleanupRequest,
): Promise<RecordingCleanupExecution> {
  return postJson("/meetings/recordings/cleanup", body);
}
