import { createServer, type Server } from "node:http";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

export interface ReplyFixture {
  directory: string;
  statePath: string;
  baseUrl: string;
  server: Server;
}

function promptText(value: unknown): string {
  if (typeof value !== "object" || value === null || !("messages" in value)) {
    throw new Error("Invalid synthetic chat request");
  }
  if (!Array.isArray(value.messages)) throw new Error("Missing messages");
  return value.messages
    .map((message: unknown) => {
      if (
        typeof message !== "object" ||
        message === null ||
        !("content" in message)
      ) {
        throw new Error("Invalid synthetic message");
      }
      if (typeof message.content === "string") return message.content;
      // Rig serializes user messages as OpenAI content parts.
      if (!Array.isArray(message.content))
        throw new Error("Expected text content");
      return message.content
        .map((part: unknown) => {
          if (
            typeof part !== "object" ||
            part === null ||
            !("type" in part) ||
            part.type !== "text" ||
            !("text" in part) ||
            typeof part.text !== "string"
          ) {
            throw new Error("Expected text part");
          }
          return part.text;
        })
        .join("\n");
    })
    .join("\n");
}

// A loopback OpenAI-compatible model keeps UI tests independent of agent installers.
export async function createReplyFixture({
  initialInvocation = 0,
}: { initialInvocation?: number } = {}): Promise<ReplyFixture> {
  const directory = await mkdtemp(
    join(tmpdir(), "meeting-supporter-wdio-reply-"),
  );
  const statePath = join(directory, "invocations");
  let invocation = initialInvocation;
  await writeFile(statePath, String(invocation), "utf-8");
  const server = createServer(async (request, response) => {
    if (request.method === "GET" && request.url === "/v1/models") {
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify({ data: [{ id: "qwen3" }] }));
      return;
    }
    if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
      response.writeHead(404).end();
      return;
    }
    try {
      const chunks: Buffer[] = [];
      let size = 0;
      for await (const chunk of request) {
        const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
        size += bytes.length;
        if (size > 1024 * 1024) throw new Error("Request too large");
        chunks.push(bytes);
      }
      const text = promptText(
        JSON.parse(Buffer.concat(chunks).toString("utf-8")),
      );
      const current = invocation++;
      await writeFile(statePath, String(invocation), "utf-8");
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      response.flushHeaders();
      // The first request stays pending until the UI cancels it.
      if (current === 0) return;
      // Normal generation also asks for a short first sentence. Only the
      // dedicated SuggestionMode::Short instruction selects the rephrased reply.
      const reply = text.split("\n").includes("返答を短い1文にしてください。")
        ? "承知しました。"
        : "準備できました。進めてください。";
      const chunk = (delta: object, finishReason: string | null) => ({
        id: "synthetic-reply",
        object: "chat.completion.chunk",
        created: 1,
        model: "qwen3",
        choices: [{ index: 0, delta, finish_reason: finishReason }],
      });
      response.write(
        `data: ${JSON.stringify(chunk({ role: "assistant", content: reply }, null))}\n\n`,
      );
      response.write(`data: ${JSON.stringify(chunk({}, "stop"))}\n\n`);
      response.end("data: [DONE]\n\n");
    } catch {
      response.writeHead(400).end();
    }
  });
  try {
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", resolve);
    });
    const address = server.address();
    if (!address || typeof address === "string")
      throw new Error("Missing fixture port");
    return {
      directory,
      statePath,
      server,
      baseUrl: `http://127.0.0.1:${address.port}/v1`,
    };
  } catch (error) {
    server.closeAllConnections();
    server.close();
    await rm(directory, { recursive: true, force: true });
    throw error;
  }
}

export async function removeReplyFixture(fixture: ReplyFixture): Promise<void> {
  fixture.server.closeAllConnections();
  await new Promise<void>((resolve, reject) =>
    fixture.server.close((error) => (error ? reject(error) : resolve())),
  );
  await rm(fixture.directory, { recursive: true, force: true });
}
