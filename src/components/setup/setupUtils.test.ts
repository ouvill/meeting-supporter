import { describe, expect, it, vi } from "vitest";
import { fileToReference, MAX_REFERENCE_FILE_BYTES } from "./setupUtils";

describe("reference ingestion", () => {
  it("rejects oversized files before reading their content", async () => {
    const file = new File([], "large.docx");
    const read = vi.fn();
    Object.defineProperties(file, {
      size: { value: MAX_REFERENCE_FILE_BYTES + 1 },
      arrayBuffer: { value: read },
    });
    expect((await fileToReference(file)).status).toBe("failed");
    expect(read).not.toHaveBeenCalled();
  });

  it("bounds inline text without splitting Unicode characters", async () => {
    const file = new File([], "notes.md");
    Object.defineProperty(file, "text", {
      value: vi.fn().mockResolvedValue("😀".repeat(40_001)),
    });
    const document = await fileToReference(file);
    expect(document.status).toBe("parsed");
    expect(document.text).toBe("😀".repeat(40_000));
  });

  it("sends DOCX as binary base64 for backend parsing", async () => {
    const file = new File([], "notes.docx");
    Object.defineProperty(file, "arrayBuffer", {
      value: vi.fn().mockResolvedValue(new Uint8Array([80, 75, 3, 4]).buffer),
    });
    const document = await fileToReference(file);
    expect(document.status).toBe("queued");
    expect(document.contentBase64).toBe("UEsDBA==");
    expect(document.text).toBeUndefined();
  });

  it("reports read failures without throwing away other selected files", async () => {
    const file = new File([], "notes.txt");
    Object.defineProperty(file, "text", {
      value: vi.fn().mockRejectedValue(new Error("synthetic read failure")),
    });
    expect(await fileToReference(file)).toMatchObject({
      status: "failed",
      error: "ファイルを読み込めませんでした",
    });
  });
});
