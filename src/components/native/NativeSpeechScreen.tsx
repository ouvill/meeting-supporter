import { useEffect, useState } from "react";
import { open, save } from "@tauri-apps/plugin-dialog";
import { Button } from "../ui/Button";
import {
  clearSpeech,
  exportSpeech,
  errorMessages,
  speechDefaults,
  speechErrorMessage,
  speechSnapshot,
  startSpeech,
  stopSpeech,
  type NativeSnapshot,
} from "../../platform/nativeSpeechClient";

const phases: Record<NativeSnapshot["phase"], string> = {
  idle: "待機中",
  starting: "音声モデルを準備しています",
  listening: "マイクから文字起こし中",
  stopping: "停止して最後の発話を処理しています",
  stopped: "停止しました",
  failed: "文字起こしが停止しました",
};

export function NativeSpeechScreen() {
  const [snapshot, setSnapshot] = useState<NativeSnapshot | null>(null);
  const [model, setModel] = useState("");
  const [punctuation, setPunctuation] = useState<string | null>(null);
  const [usePunctuation, setUsePunctuation] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connectionFailed, setConnectionFailed] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const accept = (incoming: NativeSnapshot) =>
    setSnapshot((current) =>
      !current || incoming.revision >= current.revision ? incoming : current,
    );

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    void speechDefaults()
      .then((defaults) => {
        if (!alive) return;
        setModel(defaults.model_directory);
        setPunctuation(defaults.punctuation_directory);
        setUsePunctuation(defaults.punctuation_directory !== null);
      })
      .catch(() => {
        if (alive) setError("モデルのフォルダーを選択してください。");
      });
    const refresh = async () => {
      try {
        const value = await speechSnapshot();
        if (alive) {
          accept(value);
          setConnectionFailed(false);
        }
      } catch {
        if (alive) setConnectionFailed(true);
      } finally {
        if (alive)
          timer = setTimeout(() => {
            void refresh();
          }, 400);
      }
    };
    void refresh();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);

  const active =
    snapshot !== null &&
    ["starting", "listening", "stopping"].includes(snapshot.phase);
  const action = async (operation: () => Promise<NativeSnapshot>) => {
    setPending(true);
    setError(null);
    try {
      accept(await operation());
    } catch (failure) {
      setError(speechErrorMessage(failure));
    } finally {
      setPending(false);
    }
  };
  const chooseModel = async (kind: "speech" | "punctuation") => {
    try {
      const path = await open({
        directory: true,
        multiple: false,
        title:
          kind === "speech"
            ? "ReazonSpeech のモデルフォルダー"
            : "句読点モデルのフォルダー",
      });
      if (typeof path === "string") {
        if (kind === "speech") setModel(path);
        else setPunctuation(path);
      }
    } catch {
      setError("フォルダーを選択できませんでした。");
    }
  };
  const download = async () => {
    setError(null);
    try {
      const path = await save({
        defaultPath: "meeting-transcript.txt",
        filters: [{ name: "テキスト", extensions: ["txt"] }],
      });
      if (path) await exportSpeech(path);
    } catch (failure) {
      setError(speechErrorMessage(failure));
    }
  };
  const failure =
    error ?? (snapshot?.error ? errorMessages[snapshot.error] : null);

  return (
    <main className="flex h-screen flex-col bg-paper text-ink">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-surface px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold">ローカル文字起こし</h1>
          <p role="status" className="mt-1 text-sm text-ink-muted">
            {snapshot ? phases[snapshot.phase] : "状態を確認しています…"}
          </p>
        </div>
        <div className="flex gap-2">
          {active ? (
            <Button
              variant="danger"
              disabled={pending || snapshot?.phase === "stopping"}
              onClick={() => {
                void action(stopSpeech);
              }}
            >
              停止
            </Button>
          ) : (
            <Button
              variant="primary"
              disabled={
                pending ||
                !snapshot ||
                !model ||
                connectionFailed ||
                (usePunctuation && !punctuation)
              }
              onClick={() => {
                void action(() =>
                  startSpeech(model, usePunctuation ? punctuation : null),
                );
              }}
            >
              文字起こしを開始
            </Button>
          )}
        </div>
      </header>
      <section
        aria-label="音声の設定"
        className="flex flex-wrap items-center gap-3 border-b border-line px-6 py-3 text-sm"
      >
        <span>入力：システムの既定のマイク</span>
        <Button
          size="sm"
          disabled={active || pending}
          onClick={() => {
            void chooseModel("speech");
          }}
        >
          音声モデルを選択
        </Button>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={usePunctuation}
            disabled={active || pending}
            onChange={(event) => setUsePunctuation(event.target.checked)}
          />
          句読点を付ける
        </label>
        <Button
          size="sm"
          disabled={active || pending}
          onClick={() => {
            void chooseModel("punctuation");
          }}
        >
          句読点モデルを選択
        </Button>
        {usePunctuation && !punctuation && (
          <span className="text-ink-muted">句読点モデルは未選択です</span>
        )}
      </section>
      <div className="space-y-2 px-6 pt-3">
        <p className="text-xs text-ink-muted">
          音声は外部に送信しません。文字起こしはアプリを閉じると消えます。必要な内容はテキストで保存してください。
        </p>
        <p className="text-xs text-ink-muted">
          このモードでは、会議履歴・録音保存・AI 支援はまだ利用できません。
        </p>
        {connectionFailed && (
          <p role="alert" className="text-sm text-danger">
            状態を取得できません。録音が続いている可能性があります。停止できない場合はアプリを終了してください。
          </p>
        )}
        {failure && (
          <p role="alert" className="text-sm text-danger">
            {failure}
          </p>
        )}
      </div>
      <section
        aria-label="文字起こし"
        className="min-h-0 flex-1 overflow-y-auto px-6 py-4"
      >
        {snapshot?.transcripts.length ? (
          <ol className="space-y-3">
            {snapshot.transcripts.map((line) => (
              <li
                key={line.generation + ":" + line.sequence}
                className="rounded-lg border border-line bg-surface p-4"
              >
                <p className="whitespace-pre-wrap break-words leading-relaxed">
                  {line.text}
                </p>
                {line.punctuation_failed && (
                  <p className="mt-2 text-xs text-ink-muted">
                    句読点処理に失敗したため認識原文を表示しています。
                  </p>
                )}
                {line.text !== line.raw_text && (
                  <details className="mt-2 text-xs text-ink-muted">
                    <summary className="cursor-pointer">認識原文</summary>
                    <p className="mt-2 whitespace-pre-wrap break-words">
                      {line.raw_text}
                    </p>
                  </details>
                )}
              </li>
            ))}
          </ol>
        ) : (
          <p className="py-12 text-center text-sm text-ink-muted">
            開始すると、発話が確定するたびにここへ表示されます。
          </p>
        )}
      </section>
      <footer className="flex flex-wrap items-center gap-3 border-t border-line px-6 py-3">
        <Button
          disabled={!snapshot?.transcripts.length}
          onClick={() => {
            void download();
          }}
        >
          テキストを保存
        </Button>
        {!confirmClear ? (
          <Button
            variant="quiet"
            disabled={active || pending || !snapshot?.transcripts.length}
            onClick={() => setConfirmClear(true)}
          >
            内容を消去
          </Button>
        ) : (
          <>
            <span className="text-sm">保存していない内容も消去しますか？</span>
            <Button
              variant="danger"
              disabled={active || pending}
              onClick={() => {
                setConfirmClear(false);
                void action(clearSpeech);
              }}
            >
              消去する
            </Button>
            <Button variant="quiet" onClick={() => setConfirmClear(false)}>
              キャンセル
            </Button>
          </>
        )}
      </footer>
    </main>
  );
}
