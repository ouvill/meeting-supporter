import type { MeetingContextInput, ReferenceDocumentInput } from "../../types";

export const ACCEPTED_REFERENCE_EXTENSIONS = [
  ".md",
  ".markdown",
  ".txt",
  ".docx",
];

function createDocumentId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto)
    return crypto.randomUUID();
  return `doc-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function extensionOf(name: string): string {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

function bufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export async function fileToReference(
  file: File,
): Promise<ReferenceDocumentInput> {
  const extension = extensionOf(file.name);
  if (!ACCEPTED_REFERENCE_EXTENSIONS.includes(extension)) {
    return {
      id: createDocumentId(),
      name: file.name,
      mimeType: file.type || "application/octet-stream",
      sizeBytes: file.size,
      status: "failed",
      error: "この形式のファイルは追加できません",
    };
  }

  if (extension === ".docx") {
    return {
      id: createDocumentId(),
      name: file.name,
      mimeType:
        file.type ||
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      sizeBytes: file.size,
      contentBase64: bufferToBase64(await file.arrayBuffer()),
      status: "queued",
      error: null,
    };
  }

  return {
    id: createDocumentId(),
    name: file.name,
    mimeType: file.type || "text/plain",
    sizeBytes: file.size,
    text: await file.text(),
    status: "parsed",
    error: null,
  };
}

export function contextWithFallback(
  context: MeetingContextInput,
): MeetingContextInput {
  return {
    ...context,
    scenario: context.scenario.trim() || "会議",
    userRole: context.userRole.trim() || "参加者",
    objective: context.objective.trim() || "目的未設定",
    tone: context.tone?.trim() || "簡潔で自然",
  };
}
