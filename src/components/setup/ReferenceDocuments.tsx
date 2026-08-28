import { useState, type Dispatch, type SetStateAction } from "react";
import { Trash2, Upload } from "lucide-react";
import type { ReferenceDocumentInput } from "../../types";
import { Tooltip } from "../ui";
import {
  ACCEPTED_REFERENCE_EXTENSIONS,
  fileToReference,
} from "./setupUtils";

interface ReferenceDocumentsProps {
  references: ReferenceDocumentInput[];
  onChange: Dispatch<SetStateAction<ReferenceDocumentInput[]>>;
}

export function ReferenceDocuments({
  references,
  onChange,
}: ReferenceDocumentsProps) {
  const [dragActive, setDragActive] = useState(false);
  const [referenceMessage, setReferenceMessage] = useState("");

  async function addReferenceFiles(files: FileList | File[]) {
    const incoming = Array.from(files);
    if (!incoming.length) return;
    const documents = await Promise.all(incoming.map(fileToReference));
    onChange((current) => [...current, ...documents]);
    const failed = documents.filter(
      (document) => document.status === "failed",
    ).length;
    setReferenceMessage(
      failed
        ? `${failed}件は追加できませんでした`
        : `${documents.length}件を追加しました`,
    );
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-ink">参考資料</p>
          <p className="mt-0.5 text-xs text-ink-muted">
            文書またはテキストを追加できます
          </p>
        </div>
        {referenceMessage && (
          <span className="text-xs text-ink-muted" aria-live="polite">
            {referenceMessage}
          </span>
        )}
      </div>
      <label
        onDragEnter={(event) => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => {
          event.preventDefault();
          setDragActive(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragActive(false);
          void addReferenceFiles(event.dataTransfer.files);
        }}
        className={`flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-4 text-center text-xs font-medium transition-colors motion-reduce:transition-none ${
          dragActive
            ? "border-primary bg-primary-soft text-primary"
            : "border-line-strong bg-paper text-ink-muted hover:border-primary/45 hover:text-primary"
        }`}
      >
        <Upload aria-hidden="true" size={16} />
        ドロップするか、ファイルを選ぶ
        <input
          type="file"
          multiple
          accept={ACCEPTED_REFERENCE_EXTENSIONS.join(",")}
          className="sr-only"
          onChange={(event) => {
            if (event.currentTarget.files)
              void addReferenceFiles(event.currentTarget.files);
            event.currentTarget.value = "";
          }}
        />
      </label>
      {references.length > 0 && (
        <ul className="mt-2 space-y-1.5" aria-label="追加した資料">
          {references.map((document) => (
            <li
              key={document.id}
              className="flex items-center justify-between gap-3 rounded-xl border border-line bg-paper px-3 py-2 text-xs"
            >
              <div className="min-w-0">
                <p className="truncate font-medium text-ink">{document.name}</p>
                <p
                  className={
                    document.status === "failed"
                      ? "text-danger"
                      : "text-ink-muted"
                  }
                >
                  {document.status === "failed" ? document.error : "追加済み"}
                </p>
              </div>
              <Tooltip content={`${document.name}を削除`}>
                <button
                  type="button"
                  onClick={() =>
                    onChange((current) =>
                      current.filter((item) => item.id !== document.id),
                    )
                  }
                  aria-label={`${document.name}を削除`}
                  className="rounded-lg p-1.5 text-ink-muted transition-colors hover:bg-surface hover:text-danger motion-reduce:transition-none"
                >
                  <Trash2 aria-hidden="true" size={15} />
                </button>
              </Tooltip>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
