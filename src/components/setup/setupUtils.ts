import type { MeetingContextInput, ReferenceDocumentInput } from "../../types";

export const MAX_REFERENCE_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_REFERENCE_TOTAL_BYTES = 20 * 1024 * 1024;
export const MAX_REFERENCE_COUNT = 10;

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
  const metadata = {
    id: createDocumentId(),
    name: file.name,
    mimeType: file.type || "application/octet-stream",
    sizeBytes: file.size,
  };
  if (!ACCEPTED_REFERENCE_EXTENSIONS.includes(extension)) {
    return {
      ...metadata,
      status: "failed",
      error: "この形式のファイルは追加できません",
    };
  }
  if (file.size > MAX_REFERENCE_FILE_BYTES) {
    return {
      ...metadata,
      status: "failed",
      error: "資料は1件10 MiB以内で追加してください",
    };
  }
  try {
    if (extension === ".docx") {
      return {
        ...metadata,
        mimeType:
          file.type ||
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        contentBase64: bufferToBase64(await file.arrayBuffer()),
        status: "queued",
        error: null,
      };
    }
    return {
      ...metadata,
      mimeType: file.type || "text/plain",
      text: Array.from(await file.text())
        .slice(0, 40_000)
        .join(""),
      status: "parsed",
      error: null,
    };
  } catch {
    return {
      ...metadata,
      status: "failed",
      error: "ファイルを読み込めませんでした",
    };
  }
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
