import { useEffect, useRef, useState } from "react";
import { LoaderCircle, Plus, RefreshCw } from "lucide-react";
import {
  connectAgent,
  getAgentCatalog,
  installAgent,
  removeAgent,
  selectAgentModel,
  updateAllAgents,
  type AgentCatalog,
  type RegistryAgent,
} from "../../api/agentRegistry";
import { Button } from "../ui/Button";
import { SettingsCard } from "./SettingsPrimitives";

interface Props {
  locked: boolean;
  onChanged: () => void;
}
export function AgentRegistryPanel({ locked, onChanged }: Props) {
  const [catalog, setCatalog] = useState<AgentCatalog | null>(null);
  const [browse, setBrowse] = useState(false);
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [editingAuth, setEditingAuth] = useState<string | null>(null);
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const revision = useRef(0);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    const refresh = () => {
      if (inFlight.current) return;
      const currentRevision = revision.current;
      void getAgentCatalog(false, controller.signal)
        .then((value) => {
          if (
            !controller.signal.aborted &&
            revision.current === currentRevision
          )
            setCatalog(value);
        })
        .catch(() => {
          if (!controller.signal.aborted)
            setError("エージェントの一覧を取得できませんでした。");
        });
    };
    refresh();
    const timer = locked ? undefined : window.setInterval(refresh, 15_000);
    return () => {
      mounted.current = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [locked]);

  const perform = async (label: string, action: () => Promise<void>) => {
    if (locked || inFlight.current) return;
    inFlight.current = true;
    revision.current += 1;
    setPending(label);
    setError(null);
    setMessage(null);
    try {
      await action();
    } catch (cause) {
      if (mounted.current)
        setError(
          cause instanceof Error ? cause.message : "操作に失敗しました。",
        );
    } finally {
      // Installation can succeed even when the subsequent connection fails.
      try {
        const next = await getAgentCatalog();
        if (mounted.current) {
          setCatalog(next);
          onChanged();
        }
      } catch {
        /* Preserve the original action error and allow another attempt. */
      }
      inFlight.current = false;
      if (mounted.current) setPending(null);
    }
  };
  const connect = async (agent: RegistryAgent, method?: string) => {
    const status = await connectAgent(agent.id, method);
    if (mounted.current) {
      setMessage(status.message);
      if (method && status.ready) setEditingAuth(null);
    }
  };
  const openCatalog = () =>
    void perform("一覧を取得しています", async () => {
      const next = await getAgentCatalog(true);
      if (mounted.current) {
        setCatalog(next);
        setBrowse(true);
      }
    });
  if (!catalog && !error) return null;
  const agents = (catalog?.agents ?? []).filter(
    (agent) =>
      (browse || agent.installed_version !== null) &&
      `${agent.name} ${agent.authors.join(" ")}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const disabled = locked || pending !== null;
  const installed = catalog?.agents.some(
    (agent) => agent.installed_version !== null,
  );
  return (
    <SettingsCard
      title="エージェント"
      description="追加したエージェントは、下の一覧で返答案に割り当てられます。"
    >
      <div className="space-y-3">
        <p className="text-xs text-ink-muted">
          追加すると配布元のプログラムを取得して起動します。認証と料金は各サービスで管理されます。
        </p>
        {catalog && catalog.update_count > 0 && (
          <p className="text-xs text-ink-muted">
            更新 {catalog.update_count}件
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={disabled}
            onClick={openCatalog}
          >
            {browse ? (
              <RefreshCw className="h-3 w-3" />
            ) : (
              <Plus className="h-3 w-3" />
            )}
            {browse ? "一覧を更新" : "エージェントを追加"}
          </Button>
          {installed && (
            <Button
              size="sm"
              variant="quiet"
              disabled={disabled}
              onClick={() =>
                void perform("更新を確認しています", async () => {
                  const next = await getAgentCatalog(true);
                  if (mounted.current) {
                    setCatalog(next);
                    if (next && next.update_count === 0 && !next.update_message)
                      setMessage("更新できるエージェントはありません。");
                  }
                })
              }
            >
              更新を確認
            </Button>
          )}
          {catalog && catalog.update_count > 0 && (
            <Button
              size="sm"
              variant="secondary"
              disabled={disabled}
              onClick={() =>
                void perform("エージェントを更新しています", async () => {
                  const result = await updateAllAgents();
                  if (mounted.current) {
                    const updated = result.results.filter(
                      (item) => item.updated,
                    ).length;
                    setMessage(`${updated}件更新しました。`);
                    const failures = result.results.filter(
                      (item) => item.error !== null,
                    );
                    if (failures.length)
                      setError(
                        failures
                          .map(
                            (item) =>
                              `${item.name}: ${item.error} 以前の版を保持しています。`,
                          )
                          .join("\n"),
                      );
                  }
                })
              }
            >
              まとめて更新
            </Button>
          )}
          {browse && (
            <Button
              size="sm"
              variant="quiet"
              onClick={() => {
                setBrowse(false);
                setQuery("");
              }}
            >
              導入済みだけ表示
            </Button>
          )}
        </div>
        {browse && (
          <input
            aria-label="エージェントを検索"
            placeholder="名前で検索"
            className="field w-full"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        )}
        {locked && (
          <p className="text-xs text-ink-muted">
            エージェントの追加・更新・接続は会議終了後に行えます。
          </p>
        )}
        {pending && (
          <p
            className="flex items-center gap-2 text-xs text-ink-muted"
            role="status"
          >
            <LoaderCircle className="h-3 w-3 animate-spin" />
            {pending}
          </p>
        )}
        {message && (
          <p className="text-xs text-ink-muted" role="status">
            {message}
          </p>
        )}
        {catalog?.update_message && (
          <p className="text-xs text-ink-muted">{catalog.update_message}</p>
        )}
        {error && (
          <p className="text-xs text-danger" role="alert">
            {error}
          </p>
        )}
        <div className="max-h-80 space-y-2 overflow-y-auto">
          {agents.map((agent) => (
            <div key={agent.id} className="rounded-xl border border-line p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-ink">{agent.name}</p>
                  <p className="text-xs text-ink-muted">
                    {agent.authors.join(" / ")} ·{" "}
                    {agent.installed_version
                      ? `導入済み ${agent.installed_version}`
                      : agent.version}
                  </p>
                  {agent.update_version && (
                    <p className="text-xs text-ink-muted">
                      更新版: {agent.update_version}
                    </p>
                  )}
                </div>
                <span className="text-xs text-ink-muted">
                  {agent.status.ready
                    ? "接続済み"
                    : agent.installed_version
                      ? "導入済み"
                      : "未導入"}
                </span>
              </div>
              <p className="mt-2 text-xs text-ink-muted">{agent.description}</p>
              {agent.status.ready && agent.status.model && (
                <label className="mt-3 block text-xs font-medium text-ink">
                  モデル
                  <select
                    className="field mt-1 w-full"
                    aria-label={`${agent.name}のモデル`}
                    value={agent.status.model.current}
                    disabled={disabled}
                    onChange={(event) => {
                      const model = event.target.value;
                      void perform(
                        `${agent.name}のモデルを変更しています`,
                        async () => {
                          const status = await selectAgentModel(
                            agent.id,
                            model,
                          );
                          if (mounted.current) setMessage(status.message);
                        },
                      );
                    }}
                  >
                    {agent.status.model.options.map((model) => (
                      <option key={model.id} value={model.id}>
                        {model.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {!agent.installed_version && agent.distribution === "npm" && (
                <p className="mt-2 text-xs text-ink-muted">
                  導入にはNode.jsとnpmが必要です。
                </p>
              )}
              {!agent.supported && (
                <p className="mt-2 text-xs text-ink-muted">
                  この環境では導入できません。
                </p>
              )}
              <div className="mt-2 flex flex-wrap gap-2">
                {agent.supported &&
                  (!agent.installed_version ||
                    agent.update_version !== null) && (
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={disabled}
                      onClick={() =>
                        void perform(
                          `${agent.name}を導入しています`,
                          async () => {
                            await installAgent(agent.id);
                            await connect(agent);
                          },
                        )
                      }
                    >
                      {agent.installed_version
                        ? `${agent.update_version}に更新`
                        : "追加"}
                    </Button>
                  )}
                {agent.installed_version && (
                  <>
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={disabled}
                      onClick={() =>
                        void perform(`${agent.name}に接続しています`, () =>
                          connect(agent),
                        )
                      }
                    >
                      接続を確認
                    </Button>
                    {agent.status.ready &&
                      agent.status.auth_methods.length > 0 && (
                        <Button
                          size="sm"
                          variant="quiet"
                          disabled={disabled}
                          aria-expanded={editingAuth === agent.id}
                          onClick={() =>
                            setEditingAuth(
                              editingAuth === agent.id ? null : agent.id,
                            )
                          }
                        >
                          {editingAuth === agent.id
                            ? "閉じる"
                            : "ログイン方法を変更"}
                        </Button>
                      )}
                    {(!agent.status.ready || editingAuth === agent.id) &&
                      agent.status.auth_methods.map((method) => (
                        <Button
                          key={method.id}
                          size="sm"
                          variant="primary"
                          disabled={disabled}
                          onClick={() =>
                            void perform(
                              `${agent.name}の認証を待っています`,
                              () => connect(agent, method.id),
                            )
                          }
                        >
                          {method.name}
                        </Button>
                      ))}
                    <Button
                      size="sm"
                      variant="quiet"
                      disabled={disabled}
                      onClick={() =>
                        void perform(`${agent.name}を削除しています`, () =>
                          removeAgent(agent.id),
                        )
                      }
                    >
                      削除
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
        {browse && agents.length === 0 && (
          <p className="text-xs text-ink-muted">
            該当するエージェントはありません。
          </p>
        )}
      </div>
    </SettingsCard>
  );
}
