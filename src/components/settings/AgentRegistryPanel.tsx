import { useEffect, useRef, useState } from "react";
import { LoaderCircle, RefreshCw } from "lucide-react";
import {
  connectAgent,
  getAgentCatalog,
  installAgent,
  removeAgent,
  type RegistryAgent,
} from "../../api/agentRegistry";
import { useAgentRegistryStore } from "../../store/agentRegistryStore";
import { Button } from "../ui/Button";
import { SettingsSection } from "./SettingsPrimitives";

interface Props {
  locked: boolean;
  onChanged: () => void;
  agentId?: string;
  onSelect?: (id: string) => void;
}
export function AgentRegistryPanel({
  locked,
  onChanged,
  agentId,
  onSelect,
}: Props) {
  const {
    catalog,
    catalogError,
    pending,
    error,
    message,
    refresh,
    perform: performTask,
  } = useAgentRegistryStore();
  const setCatalog = (catalog: Awaited<ReturnType<typeof getAgentCatalog>>) =>
    useAgentRegistryStore.setState({ catalog });
  const setMessage = (message: string | null) =>
    useAgentRegistryStore.setState({ message });
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [editingAuth, setEditingAuth] = useState<string | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    const poll = () => void refresh(controller.signal);
    setLoading(true);
    const initial = refresh(controller.signal, !agentId && !locked);
    void initial.finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    const timer = locked ? undefined : window.setInterval(poll, 15_000);
    return () => {
      mounted.current = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [locked, refresh, agentId]);

  const perform = (label: string, action: () => Promise<void>) => {
    if (locked) return;
    return performTask(label, action, onChanged);
  };
  const connect = async (agent: RegistryAgent, method?: string) => {
    const status = await connectAgent(agent.id, method);
    setMessage(status.message);
    if (mounted.current && method && status.ready) setEditingAuth(null);
  };
  const openCatalog = async () => {
    setLoading(true);
    await refresh(new AbortController().signal, true);
    if (mounted.current) setLoading(false);
  };
  const agents = (catalog?.agents ?? []).filter(
    (agent) =>
      (!agentId || agent.id === agentId) &&
      `${agent.name} ${agent.authors.join(" ")}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const disabled = locked || pending !== null || loading;
  return (
    <SettingsSection
      title={agentId ? undefined : "追加するAIエージェントを選ぶ"}
      description={
        agentId
          ? "接続・更新・削除は、その場で実行します。"
          : "追加したAIは「このAIを設定」から選択して、ログインできます。"
      }
    >
      <div className="space-y-3">
        {!agentId && (
          <>
            <p className="text-xs text-ink-muted">
              追加すると配布元のプログラムを取得して起動します。認証と料金は各サービスで管理されます。
            </p>
            <input
              aria-label="エージェントを検索"
              placeholder="名前で検索"
              className="field w-full"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <Button
              size="sm"
              variant="quiet"
              disabled={disabled}
              onClick={openCatalog}
            >
              <RefreshCw className="h-3 w-3" aria-hidden="true" />
              一覧を更新
            </Button>
          </>
        )}
        {loading && !catalog && !pending && (
          <p role="status">読み込んでいます…</p>
        )}
        {!loading && !catalog && !catalogError && (
          <p className="text-sm text-ink-muted">
            この環境ではAIエージェントを利用できません。
          </p>
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
            {pending}。設定を閉じても処理は続きます。
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
        {catalogError && (
          <div className="space-y-2" role="alert">
            <p className="text-xs text-danger">{catalogError}</p>
            <Button
              size="sm"
              variant="secondary"
              disabled={disabled}
              onClick={openCatalog}
            >
              再試行
            </Button>
          </div>
        )}
        <div className="divide-y divide-line">
          {agents.map((agent) => (
            <div key={agent.id} className="py-5">
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
                {!agentId && !agent.installed_version && agent.supported && (
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
                    追加
                  </Button>
                )}
                {!agentId && agent.installed_version && (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={disabled}
                    onClick={() => onSelect?.(agent.id)}
                  >
                    このAIを設定
                  </Button>
                )}
                {agentId && agent.installed_version && (
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
                  </>
                )}
              </div>
              {agentId && agent.installed_version && (
                <details className="mt-4 border-t border-line pt-4">
                  <summary className="cursor-pointer text-sm font-medium">
                    更新・削除
                  </summary>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="quiet"
                      disabled={disabled}
                      onClick={() =>
                        void perform("更新を確認しています", async () => {
                          const next = await getAgentCatalog(true);
                          setCatalog(next);
                          if (
                            next &&
                            !next.agents.find((item) => item.id === agentId)
                              ?.update_version &&
                            !next.update_message
                          )
                            setMessage("このエージェントは最新版です。");
                        })
                      }
                    >
                      更新を確認
                    </Button>
                    {agent.supported && agent.update_version && (
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={disabled}
                        onClick={() =>
                          void perform(
                            `${agent.name}を更新しています`,
                            async () => {
                              await installAgent(agent.id);
                              await connect(agent);
                            },
                          )
                        }
                      >
                        {agent.update_version}に更新
                      </Button>
                    )}
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
                  </div>
                </details>
              )}
            </div>
          ))}
        </div>
        {catalog && !loading && agents.length === 0 && (
          <p className="text-xs text-ink-muted">
            該当するエージェントはありません。
          </p>
        )}
      </div>
    </SettingsSection>
  );
}
