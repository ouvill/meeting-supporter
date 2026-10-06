// @vitest-environment node
import { afterEach, describe, expect, it } from "vitest";
import {
  createReplyFixture,
  removeReplyFixture,
  type ReplyFixture,
} from "./replyFixture";

let fixture: ReplyFixture | undefined;

afterEach(async () => {
  if (fixture) await removeReplyFixture(fixture);
  fixture = undefined;
});

describe("reply fixture mode detection", () => {
  it.each([
    ["自然な返答にしてください。", "準備できました。進めてください。"],
    ["返答を短い1文にしてください。", "承知しました。"],
  ])("responds to the explicit mode: %s", async (mode, expected) => {
    fixture = await createReplyFixture({ initialInvocation: 1 });
    const response = await fetch(`${fixture.baseUrl}/chat/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: [
          {
            role: "system",
            content: [
              "最初の短い1文だけで話し始められるように、結論や必要な確認を先に述べてください。",
              "補足が必要な場合だけ短い1文を続け、全体を1〜2文にしてください。",
              mode,
            ].join("\n"),
          },
          {
            role: "user",
            content: [{ type: "text", text: "合成の会話です。" }],
          },
        ],
      }),
    });
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("text/event-stream");
    const stream = await response.text();
    expect(stream).toContain(`"content":"${expected}"`);
    expect(stream).toContain("data: [DONE]");
  });
});
