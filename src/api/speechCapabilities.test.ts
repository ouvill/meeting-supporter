import { afterEach, describe, expect, it, vi } from "vitest";
import { client } from "./generated/client.gen";
import { getSpeechCapabilities } from "./speechCapabilities";

describe("getSpeechCapabilities", () => {
  const originalConfig = client.getConfig();
  afterEach(() => client.setConfig(originalConfig));

  function respond(body: unknown, status = 200) {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );
    client.setConfig({
      baseUrl: "http://localhost:1420",
      headers: { Authorization: "Bearer synthetic-token" },
      fetch,
    });
    return fetch;
  }

  it("uses the configured authenticated connection", async () => {
    const fetch = respond({ whisper_gpu: false });
    const signal = new AbortController().signal;
    await expect(getSpeechCapabilities(signal)).resolves.toEqual({
      whisper_gpu: false,
    });
    const request = fetch.mock.calls[0][0];
    expect(request).toBeInstanceOf(Request);
    if (!(request instanceof Request)) throw new Error("Expected Request");
    expect(request.url).toBe("http://localhost:1420/api/stt/capabilities");
    expect(request.headers.get("Authorization")).toBe("Bearer synthetic-token");
  });

  it.each([{}, { whisper_gpu: "false" }, { whisper_gpu: 1 }])(
    "rejects malformed capabilities %j",
    async (body) => {
      respond(body);
      await expect(
        getSpeechCapabilities(new AbortController().signal),
      ).rejects.toThrow();
    },
  );

  it("rejects unsuccessful responses even if the body looks valid", async () => {
    respond({ whisper_gpu: true }, 500);
    await expect(
      getSpeechCapabilities(new AbortController().signal),
    ).rejects.toThrow();
  });
});
