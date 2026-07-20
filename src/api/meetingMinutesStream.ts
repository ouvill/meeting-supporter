import { client } from "./generated/client.gen";

/** Start the canonical persisted-minutes stream using the configured local API auth. */
export function streamMeetingMinutes(meetingId: string, signal: AbortSignal) {
  const config = client.getConfig();
  const baseUrl = config.baseUrl ?? "";
  const headers = new Headers();
  const configuredHeaders = config.headers;
  if (configuredHeaders instanceof Headers) {
    configuredHeaders.forEach((value, name) => headers.set(name, value));
  } else if (Array.isArray(configuredHeaders)) {
    for (const [name, value] of configuredHeaders) headers.set(name, value);
  } else if (configuredHeaders) {
    for (const [name, value] of Object.entries(configuredHeaders)) {
      if (typeof value === "string") headers.set(name, value);
    }
  }

  return fetch(`${baseUrl}/meetings/${encodeURIComponent(meetingId)}/minutes`, {
    method: "POST",
    headers,
    signal,
  });
}
