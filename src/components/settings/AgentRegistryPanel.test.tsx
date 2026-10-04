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
  it("opens the catalog directly, searches it, and offers installation only for supported agents", async () => {
    vi.mocked(getAgentCatalog).mockResolvedValue(
      catalog([
        agent,
        {
          ...agent,
          id: "unsupported",
          name: "Unavailable Agent",
          supported: false,
          distribution: "unsupported",
        },
      ]),
    );
    render(
      <AgentRegistryPanel
        locked={false}
        onChanged={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    expect(await screen.findByText("Synthetic Agent")).toBeVisible();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "追加" })).toBeEnabled(),
    );
    expect(getAgentCatalog).toHaveBeenCalledWith(true, expect.any(AbortSignal));
    expect(screen.getAllByRole("button", { name: "追加" })).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: "接続を確認" }),
    ).not.toBeInTheDocument();
    fireEvent.change(
      screen.getByRole("textbox", { name: "エージェントを検索" }),
      { target: { value: "Unavailable" } },
    );
    expect(screen.queryByText("Synthetic Agent")).not.toBeInTheDocument();
    expect(screen.getByText("この環境では導入できません。")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "追加" }),
    ).not.toBeInTheDocument();
    expect(installAgent).not.toHaveBeenCalled();
  });

  it("explains an unavailable catalog", async () => {
    vi.mocked(getAgentCatalog).mockResolvedValue(null);
    render(<AgentRegistryPanel locked={false} onChanged={vi.fn()} />);
    expect(
      await screen.findByText("この環境ではAIエージェントを利用できません。"),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "追加" }),
    ).not.toBeInTheDocument();
  });

  it("keeps a successful installation after connection failure and hands off to the selected AI's connection settings", async () => {
    let installed = false;
    vi.mocked(getAgentCatalog).mockImplementation(async () =>
      catalog([
        {
          ...agent,
          installed_version: installed ? agent.version : null,
          status: {
            ready: false,
            message: "",
            auth_methods: [{ id: "login", name: "テスト認証" }],
          },
        },
      ]),
    );
    vi.mocked(installAgent).mockImplementation(async () => {
      installed = true;
    });
    vi.mocked(connectAgent).mockRejectedValueOnce(
      new Error("接続できませんでした。"),
    );
    const onSelect = vi.fn();
    const onChanged = vi.fn();
    const view = render(
      <AgentRegistryPanel
        locked={false}
        onChanged={onChanged}
        onSelect={onSelect}
      />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "追加" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "追加" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "接続できませんでした。",
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "このAIを設定" }),
    );
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("synthetic");
    view.rerender(
      <AgentRegistryPanel
        agentId="synthetic"
        locked={false}
        onChanged={onChanged}
      />,
    );
    vi.mocked(connectAgent).mockResolvedValue({
      ready: true,
      message: "接続済みです。",
      auth_methods: [],
    });
    fireEvent.click(await screen.findByRole("button", { name: "テスト認証" }));
    expect(await screen.findByText("接続済みです。")).toBeVisible();
    expect(connectAgent).toHaveBeenLastCalledWith("synthetic", "login");
    expect(installAgent).toHaveBeenCalledExactlyOnceWith("synthetic");
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it("shows only the chosen AI, without duplicating model selection, and places updates and removal in details", async () => {
    vi.mocked(getAgentCatalog).mockResolvedValue(
      catalog([
        {
          ...agent,
          installed_version: "0.9.0",
          update_version: "1.0.0",
          status: {
            ready: true,
            message: "",
            auth_methods: [],
            model: { current: "fast", options: [{ id: "fast", name: "Fast" }] },
          },
        },
        {
          ...agent,
          id: "other",
          name: "Other Agent",
          installed_version: "1.0.0",
        },
      ]),
    );
    render(
      <AgentRegistryPanel
        agentId="synthetic"
        locked={false}
        onChanged={vi.fn()}
      />,
    );
    expect(await screen.findByText("Synthetic Agent")).toBeVisible();
    expect(screen.queryByText("Other Agent")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "削除" })).not.toBeVisible();
    fireEvent.click(screen.getByText("更新・削除"));
    expect(screen.getByRole("button", { name: "1.0.0に更新" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "削除" }));
    await waitFor(() =>
      expect(removeAgent).toHaveBeenCalledExactlyOnceWith("synthetic"),
    );
    expect(selectAgentModel).not.toHaveBeenCalled();
  });

  it("locks installation, connection, authentication, updates and removal during a meeting", async () => {
    vi.mocked(getAgentCatalog).mockResolvedValue(
      catalog([
        {
          ...agent,
          installed_version: "0.9.0",
          update_version: "1.0.0",
          status: {
            ready: false,
            message: "",
            auth_methods: [{ id: "login", name: "テスト認証" }],
          },
        },
      ]),
    );
    render(
      <AgentRegistryPanel agentId="synthetic" locked onChanged={vi.fn()} />,
    );
    await screen.findByText("Synthetic Agent");
    fireEvent.click(screen.getByText("更新・削除"));
    for (const button of screen.getAllByRole("button"))
      expect(button).toBeDisabled();
    expect(installAgent).not.toHaveBeenCalled();
    expect(connectAgent).not.toHaveBeenCalled();
    expect(removeAgent).not.toHaveBeenCalled();
  });

  it("allows changing the login method and retrying rejected authentication", async () => {
    let ready = true;
    const status = () => ({
      ready,
      message: ready ? "接続済みです。" : "ログインが必要です。",
      auth_methods: [{ id: "alternate", name: "別のテスト認証" }],
    });
    vi.mocked(getAgentCatalog).mockImplementation(async () =>
      catalog([{ ...agent, installed_version: "1.0.0", status: status() }]),
    );
    vi.mocked(connectAgent).mockImplementation(async () => {
      ready = !ready;
      return status();
    });
    render(
      <AgentRegistryPanel
        agentId="synthetic"
        locked={false}
        onChanged={vi.fn()}
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "ログイン方法を変更" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "別のテスト認証" }));
    expect(await screen.findByText("ログインが必要です。")).toBeVisible();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "別のテスト認証" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "別のテスト認証" }));
    expect(
      await screen.findByRole("button", { name: "ログイン方法を変更" }),
    ).toHaveAttribute("aria-expanded", "false");
    expect(connectAgent).toHaveBeenLastCalledWith("synthetic", "alternate");
  });

  it("checks updates for the selected AI and does not offer unavailable releases", async () => {
    vi.mocked(getAgentCatalog).mockResolvedValue(
      catalog([{ ...agent, installed_version: "1.0.0" }]),
    );
    render(
      <AgentRegistryPanel
        agentId="synthetic"
        locked={false}
        onChanged={vi.fn()}
      />,
    );
    await screen.findByText("Synthetic Agent");
    fireEvent.click(screen.getByText("更新・削除"));
    fireEvent.click(screen.getByRole("button", { name: "更新を確認" }));
    expect(
      await screen.findByText("このエージェントは最新版です。"),
    ).toBeVisible();
    expect(getAgentCatalog).toHaveBeenCalledWith(true);
    expect(
      screen.queryByRole("button", { name: "1.0.0に更新" }),
    ).not.toBeInTheDocument();
    expect(installAgent).not.toHaveBeenCalled();
  });

  it("continues an update after closing and prevents duplicate updates on reopening", async () => {
    let finish!: () => void;
    vi.mocked(getAgentCatalog).mockResolvedValue(
      catalog([
        { ...agent, installed_version: "0.9.0", update_version: "1.0.0" },
      ]),
    );
    vi.mocked(installAgent).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    vi.mocked(connectAgent).mockResolvedValue({
      ready: true,
      message: "接続済みです。",
      auth_methods: [],
    });
    const onChanged = vi.fn();
    const first = render(
      <AgentRegistryPanel
        agentId="synthetic"
        locked={false}
        onChanged={onChanged}
      />,
    );
    await screen.findByText("Synthetic Agent");
    fireEvent.click(screen.getByText("更新・削除"));
    fireEvent.click(screen.getByRole("button", { name: "1.0.0に更新" }));
    first.unmount();
    const next = render(
      <AgentRegistryPanel
        agentId="synthetic"
        locked={false}
        onChanged={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("更新・削除"));
    expect(screen.getByRole("button", { name: "1.0.0に更新" })).toBeDisabled();
    expect(installAgent).toHaveBeenCalledOnce();
    next.unmount();
    await act(async () => {
      finish();
    });
    expect(connectAgent).toHaveBeenCalledExactlyOnceWith(
      "synthetic",
      undefined,
    );
    expect(onChanged).toHaveBeenCalledOnce();
    expect(useSettingsTaskStore.getState().tasks.agents).toMatchObject({
      state: "completed",
    });
  });

  it("preserves the installed version after update failure and permits retry", async () => {
    vi.mocked(getAgentCatalog).mockResolvedValue(
      catalog([
        { ...agent, installed_version: "0.9.0", update_version: "1.0.0" },
      ]),
    );
    vi.mocked(installAgent).mockRejectedValue(
      new Error("更新に失敗しました。"),
    );
    const first = render(
      <AgentRegistryPanel
        agentId="synthetic"
        locked={false}
        onChanged={vi.fn()}
      />,
    );
    await screen.findByText("Synthetic Agent");
    fireEvent.click(screen.getByText("更新・削除"));
    fireEvent.click(screen.getByRole("button", { name: "1.0.0に更新" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "更新に失敗しました。",
    );
    first.unmount();
    render(
      <AgentRegistryPanel
        agentId="synthetic"
        locked={false}
        onChanged={vi.fn()}
      />,
    );
    await screen.findByText("Synthetic Agent");
    expect(screen.getByRole("alert")).toHaveTextContent("更新に失敗しました。");
    expect(screen.getByText(/導入済み 0\.9\.0/)).toBeVisible();
    fireEvent.click(screen.getByText("更新・削除"));
    expect(screen.getByRole("button", { name: "1.0.0に更新" })).toBeEnabled();
  });
});
