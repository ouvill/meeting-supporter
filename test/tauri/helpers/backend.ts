import { browser } from "@wdio/globals";

export interface BackendRequest {
  path: string;
  method?: "GET" | "POST" | "PUT";
  body?: unknown;
}

export interface WaitOptions {
  timeout?: number;
  interval?: number;
  timeoutMsg?: string;
}

export async function localBackendRequest<T>(
  request: BackendRequest,
): Promise<T> {
  return browser.tauri.execute(async ({ core }, rawRequest: BackendRequest) => {
    const [port, token] = await Promise.all([
      core.invoke("get_api_port"),
      core.invoke("get_api_auth_token"),
    ]);
    if (typeof port !== "number" || typeof token !== "string" || !token) {
      throw new Error("Tauri backend endpoint is unavailable");
    }
    const response = await fetch(`http://127.0.0.1:${port}${rawRequest.path}`, {
      method: rawRequest.method ?? "GET",
      headers: {
        Authorization: `Bearer ${token}`,
        ...(rawRequest.body === undefined
          ? {}
          : { "Content-Type": "application/json" }),
      },
      ...(rawRequest.body === undefined
        ? {}
        : { body: JSON.stringify(rawRequest.body) }),
    });
    if (!response.ok) {
      throw new Error(
        `Local backend request failed with HTTP ${response.status}`,
      );
    }
    return response.json();
  }, request) as Promise<T>;
}

export async function waitForBackendReady(
  options: WaitOptions = {},
): Promise<void> {
  await browser.waitUntil(
    async () =>
      browser.tauri.execute(async ({ core }) => {
        const [running, port, token] = await Promise.all([
          core.invoke("is_backend_running"),
          core.invoke("get_api_port"),
          core.invoke("get_api_auth_token"),
        ]);
        return (
          running === true &&
          typeof port === "number" &&
          port > 0 &&
          typeof token === "string" &&
          token.length > 0
        );
      }),
    {
      timeout: 120_000,
      interval: 200,
      timeoutMsg: "Tauri backend did not become ready",
      ...options,
    },
  );
}
