import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  connectAgent,
  getAgentCatalog,
  installAgent,
  removeAgent,
  selectAgentModel,
  updateAllAgents,
  type RegistryAgent,
} from "../../api/agentRegistry";
import { AgentRegistryPanel } from "./AgentRegistryPanel";
import { useAgentRegistryStore } from "../../store/agentRegistryStore";
import { useSettingsTaskStore } from "../../store/settingsTaskStore";

vi.mock("../../api/agentRegistry", () => ({
  getAgentCatalog: vi.fn(),
  connectAgent: vi.fn(),
  installAgent: vi.fn(),
  removeAgent: vi.fn(),
  selectAgentModel: vi.fn(),
  updateAllAgents: vi.fn(),
}));
const agent: RegistryAgent = {
  id: "synthetic",
  name: "Synthetic Agent",
  description: "Synthetic fixture",
  authors: ["Synthetic publisher"],
  version: "1.0.0",
  installed_version: null,
  update_version: null,
  supported: true,
  distribution: "npm",
  status: { ready: false, message: "", auth_methods: [] },
};
const catalog = (agents: RegistryAgent[]) => ({
  supported: true as const,
  agents,
  update_count: agents.filter((agent) => agent.update_version !== null).length,
  checked_at: null,
  update_message: null,
});

beforeEach(() => {
  vi.resetAllMocks();
  useAgentRegistryStore.setState(useAgentRegistryStore.getInitialState(), true);
  useSettingsTaskStore.setState({ tasks: {} });
});

describe("AgentRegistryPanel", () => {
  it("does not offer Registry controls when the catalog is unavailable", async () => {
    vi.mocked(getAgentCatalog).mockResolvedValue(null);
    const { container } = render(
      <AgentRegistryPanel locked={false} onChanged={vi.fn()} />,
    );
    await act(async () => {});
    expect(container).toBeEmptyDOMElement();
  });

  it("installs explicitly, keeps the installation visible after connection failure, and retries authentication", async () => {
    let installed = false;
    let browsed = false;
    let ready = false;
    vi.mocked(getAgentCatalog).mockImplementation(async (refresh) => {
      browsed ||= refresh ?? false;
      return catalog(
        installed
          ? [
              {
                ...agent,
                installed_version: agent.version,
                status: {
                  ready,
                  message: "",
                  auth_methods: [{ id: "login", name: "テスト認証" }],
                },
              },
            ]
          : browsed
            ? [agent]
            : [],
      );
    });
    vi.mocked(installAgent).mockImplementation(async () => {
      installed = true;
    });
    vi.mocked(connectAgent)
      .mockRejectedValueOnce(new Error("接続できませんでした。"))
      .mockImplementation(async () => {
        ready = true;
        return { ready: true, message: "接続済みです。", auth_methods: [] };
      });
    const onChanged = vi.fn();
    render(<AgentRegistryPanel locked={false} onChanged={onChanged} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "エージェントを追加" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "追加" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "接続できませんでした。",
    );
    expect(
      await screen.findByRole("button", { name: "接続を確認" }),
    ).toBeEnabled();
    expect(installAgent).toHaveBeenCalledExactlyOnceWith("synthetic");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "テスト認証" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "テスト認証" }));
    expect(await screen.findByText("接続済み")).toBeInTheDocument();
    expect(connectAgent).toHaveBeenLastCalledWith("synthetic", "login");
    expect(onChanged).toHaveBeenCalled();
  });

  it("locks installation, authentication and removal during a meeting", async () => {
    vi.mocked(getAgentCatalog).mockResolvedValue(
      catalog([
        {
          ...agent,
          installed_version: "0.9.0",
          status: {
            ready: false,
            message: "",
            auth_methods: [{ id: "login", name: "テスト認証" }],
          },
        },
      ]),
    );
    render(<AgentRegistryPanel locked onChanged={vi.fn()} />);
    await screen.findByText("Synthetic Agent");
    for (const button of screen.getAllByRole("button"))
      expect(button).toBeDisabled();
    expect(installAgent).not.toHaveBeenCalled();
    expect(connectAgent).not.toHaveBeenCalled();
    expect(removeAgent).not.toHaveBeenCalled();
  });

  it("selects an ACP-advertised model and refreshes the persisted selection", async () => {
    let current = "fast";
    const configured = () => ({
      ...agent,
      installed_version: agent.version,
      status: {
        ready: true,
        message: "",
        auth_methods: [],
        model: {
          current,
          options: [
            { id: "fast", name: "Fast" },
            { id: "accurate", name: "Accurate" },
          ],
        },
      },
    });
    vi.mocked(getAgentCatalog).mockImplementation(async () =>
      catalog([configured()]),
    );
    vi.mocked(selectAgentModel).mockImplementation(async (_id, model) => {
      current = model;
      return {
        ready: true,
        message: "モデルを変更しました。",
        auth_methods: [],
        model: configured().status.model,
      };
    });

    render(<AgentRegistryPanel locked={false} onChanged={vi.fn()} />);
    const select = await screen.findByRole("combobox", {
      name: "Synthetic Agentのモデル",
    });
    fireEvent.change(select, { target: { value: "accurate" } });

    await waitFor(() =>
      expect(selectAgentModel).toHaveBeenCalledWith("synthetic", "accurate"),
    );
    await waitFor(() => expect(select).toHaveValue("accurate"));
    expect(
      await screen.findByText("モデルを変更しました。"),
    ).toBeInTheDocument();
  });

  it("shows the resolved version and allows retrying an update that still resolves to an older version", async () => {
    vi.mocked(getAgentCatalog).mockResolvedValue(
      catalog([
        { ...agent, installed_version: "0.9.0", update_version: "1.0.0" },
      ]),
    );
    vi.mocked(connectAgent).mockResolvedValue({
      ready: true,
      message: "接続済みです。",
      auth_methods: [],
    });
    render(<AgentRegistryPanel locked={false} onChanged={vi.fn()} />);
    expect(await screen.findByText(/導入済み 0\.9\.0/)).toBeInTheDocument();
    expect(screen.getByText("更新版: 1.0.0")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "1.0.0に更新" }));
    await waitFor(() =>
      expect(installAgent).toHaveBeenCalledExactlyOnceWith("synthetic"),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "1.0.0に更新" })).toBeEnabled(),
    );
    expect(screen.getByText(/導入済み 0\.9\.0/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("allows choosing another login method after connecting and retrying a failed change", async () => {
    const auth_methods = [
      { id: "login", name: "テスト認証" },
      { id: "alternate", name: "別のテスト認証" },
    ];
    let ready = true;
    vi.mocked(getAgentCatalog).mockImplementation(async () =>
      catalog([
        {
          ...agent,
          installed_version: agent.version,
          status: { ready, message: "", auth_methods },
        },
      ]),
    );
    vi.mocked(connectAgent)
      .mockImplementationOnce(async () => {
        ready = false;
        return { ready, message: "ログインが必要です。", auth_methods };
      })
      .mockImplementationOnce(async () => {
        ready = true;
        return { ready, message: "接続済みです。", auth_methods };
      });
    const { rerender } = render(
      <AgentRegistryPanel locked={false} onChanged={vi.fn()} />,
    );
    const change = await screen.findByRole("button", {
      name: "ログイン方法を変更",
    });
    expect(
      screen.queryByRole("button", { name: "別のテスト認証" }),
    ).not.toBeInTheDocument();
    fireEvent.click(change);
    expect(connectAgent).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
    expect(
      screen.queryByRole("button", { name: "別のテスト認証" }),
    ).not.toBeInTheDocument();
    fireEvent.click(change);
    fireEvent.click(screen.getByRole("button", { name: "別のテスト認証" }));
    expect(await screen.findByText("ログインが必要です。")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "別のテスト認証" }),
      ).toBeEnabled(),
    );
    expect(connectAgent).toHaveBeenLastCalledWith("synthetic", "alternate");
    fireEvent.click(screen.getByRole("button", { name: "別のテスト認証" }));
    expect(
      await screen.findByRole("button", { name: "ログイン方法を変更" }),
    ).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("button", { name: "別のテスト認証" }),
    ).not.toBeInTheDocument();
    expect(installAgent).not.toHaveBeenCalled();
    await act(async () =>
      rerender(<AgentRegistryPanel locked onChanged={vi.fn()} />),
    );
    expect(
      screen.getByRole("button", { name: "ログイン方法を変更" }),
    ).toBeDisabled();
  });

  it("keeps unsupported distributions visible without offering installation", async () => {
    vi.mocked(getAgentCatalog).mockResolvedValue(
      catalog([{ ...agent, supported: false, distribution: "unsupported" }]),
    );
    render(<AgentRegistryPanel locked={false} onChanged={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "エージェントを追加" }),
    );
    expect(
      await screen.findByText("この環境では導入できません。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "追加" }),
    ).not.toBeInTheDocument();
  });

  it("shows only installable updates and reports partial batch failure while preserving the old version", async () => {
    let updated = false;
    vi.mocked(getAgentCatalog).mockImplementation(async () =>
      catalog([
        {
          ...agent,
          id: "first",
          name: "First Agent",
          installed_version: "0.9.0",
          update_version: "1.0.0",
        },
        {
          ...agent,
          id: "second",
          name: "Second Agent",
          installed_version: updated ? "1.0.0" : "0.9.0",
          update_version: updated ? null : "1.0.0",
        },
        {
          ...agent,
          id: "waiting",
          name: "Waiting Agent",
          installed_version: "0.9.0",
          update_version: null,
        },
      ]),
    );
    vi.mocked(updateAllAgents).mockImplementation(async () => {
      updated = true;
      return {
        results: [
          {
            id: "first",
            name: "First Agent",
            updated: false,
            error: "接続できませんでした。",
          },
          { id: "second", name: "Second Agent", updated: true, error: null },
        ],
      };
    });
    const { rerender } = render(
      <AgentRegistryPanel locked={false} onChanged={vi.fn()} />,
    );
    expect(await screen.findByText("更新 2件")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "1.0.0に更新" })).toHaveLength(
      2,
    );
    expect(updateAllAgents).not.toHaveBeenCalled();
    rerender(<AgentRegistryPanel locked onChanged={vi.fn()} />);
    expect(screen.getByRole("button", { name: "まとめて更新" })).toBeDisabled();
    rerender(<AgentRegistryPanel locked={false} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "まとめて更新" }));
    expect(await screen.findByText("1件更新しました。")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "First Agent: 接続できませんでした。 以前の版を保持しています。",
    );
    expect(await screen.findByText("更新 1件")).toBeInTheDocument();
    expect(updateAllAgents).toHaveBeenCalledOnce();
  });

  it("does not offer updates while the only newer release is unavailable", async () => {
    vi.mocked(getAgentCatalog).mockResolvedValue(
      catalog([{ ...agent, installed_version: "0.9.0" }]),
    );
    render(<AgentRegistryPanel locked={false} onChanged={vi.fn()} />);
    await screen.findByText("Synthetic Agent");
    expect(
      screen.queryByRole("button", { name: "まとめて更新" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "1.0.0に更新" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "更新を確認" }));
    expect(
      await screen.findByText("更新できるエージェントはありません。"),
    ).toBeInTheDocument();
    expect(getAgentCatalog).toHaveBeenCalledWith(true);
    expect(installAgent).not.toHaveBeenCalled();
  });
  it("continues installation and connection after closing, restores pending state, and blocks duplicate updates", async () => {
    let finishInstall!: () => void;
    const installation = new Promise<void>((resolve) => {
      finishInstall = resolve;
    });
    vi.mocked(getAgentCatalog).mockResolvedValue(
      catalog([
        { ...agent, installed_version: "0.9.0", update_version: "1.0.0" },
      ]),
    );
    vi.mocked(installAgent).mockReturnValue(installation);
    vi.mocked(connectAgent).mockResolvedValue({
      ready: true,
      message: "接続済みです。",
      auth_methods: [],
    });
    const onChanged = vi.fn();
    const first = render(
      <AgentRegistryPanel locked={false} onChanged={onChanged} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "1.0.0に更新" }));
    first.unmount();
    const reopened = render(
      <AgentRegistryPanel locked={false} onChanged={vi.fn()} />,
    );
    expect(
      screen.getByText(/Synthetic Agentを導入しています/),
    ).toBeInTheDocument();
    const update = screen.getByRole("button", { name: "1.0.0に更新" });
    expect(update).toBeDisabled();
    fireEvent.click(update);
    expect(installAgent).toHaveBeenCalledOnce();
    reopened.unmount();
    await act(async () => {
      finishInstall();
    });
    expect(connectAgent).toHaveBeenCalledExactlyOnceWith(
      "synthetic",
      undefined,
    );
    expect(onChanged).toHaveBeenCalledOnce();
    expect(useSettingsTaskStore.getState().tasks.agents).toMatchObject({
      state: "completed",
      message: "接続済みです。",
    });
    render(<AgentRegistryPanel locked={false} onChanged={vi.fn()} />);
    expect(await screen.findByText("接続済みです。")).toBeInTheDocument();
    await act(async () => {});
  });

  it("retains a failed background update for the next visit and permits retrying", async () => {
    let rejectUpdate!: (error: Error) => void;
    vi.mocked(getAgentCatalog).mockResolvedValue(
      catalog([
        { ...agent, installed_version: "0.9.0", update_version: "1.0.0" },
      ]),
    );
    vi.mocked(updateAllAgents).mockReturnValue(
      new Promise((_resolve, reject) => {
        rejectUpdate = reject;
      }),
    );
    const first = render(
      <AgentRegistryPanel locked={false} onChanged={vi.fn()} />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "まとめて更新" }),
    );
    first.unmount();
    await act(async () => {
      rejectUpdate(new Error("更新に失敗しました。"));
    });
    expect(useSettingsTaskStore.getState().tasks.agents).toMatchObject({
      state: "failed",
      message: "更新に失敗しました。",
    });
    render(<AgentRegistryPanel locked={false} onChanged={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "更新に失敗しました。",
    );
    expect(screen.getByRole("button", { name: "まとめて更新" })).toBeEnabled();
    await act(async () => {});
  });
});
