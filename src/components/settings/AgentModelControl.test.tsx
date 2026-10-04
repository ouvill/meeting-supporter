import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getAgentCatalog,
  selectAgentModel,
  selectAgentThoughtLevel,
  type AgentCatalog,
} from "../../api/agentRegistry";
import { useAgentRegistryStore } from "../../store/agentRegistryStore";
import { useAgentModelDrafts } from "./useAgentModelDrafts";
import { AgentModelControl } from "./AgentModelControl";

vi.mock("../../api/agentRegistry", () => ({
  getAgentCatalog: vi.fn(),
  selectAgentModel: vi.fn(),
  selectAgentThoughtLevel: vi.fn(),
}));

function fixture(): AgentCatalog {
  return {
    supported: true,
    checked_at: null,
    update_count: 0,
    update_message: null,
    agents: [
      {
        id: "synthetic",
        name: "Synthetic Agent",
        description: "Synthetic ACP agent",
        authors: [],
        version: "1.0.0",
        installed_version: "1.0.0",
        update_version: null,
        supported: true,
        distribution: "binary",
        status: {
          ready: true,
          message: "",
          auth_methods: [],
          model: {
            current: "flexible",
            options: [
              { id: "flexible", name: "Flexible" },
              { id: "fixed", name: "Fixed" },
            ],
          },
          thought_level: {
            current: "deliberate",
            options: [
              { id: "brief", name: "Quick" },
              { id: "deliberate", name: "Thorough" },
            ],
          },
        },
      },
    ],
  };
}

function Harness({ locked = false }: { locked?: boolean }) {
  const settings = useAgentModelDrafts(locked);
  return (
    <>
      <AgentModelControl
        routeId="acp:synthetic"
        locked={locked}
        settings={settings}
      />
      <button onClick={() => void settings.save()} disabled={!settings.dirty}>
        保存
      </button>
      <button onClick={settings.discard}>破棄</button>
    </>
  );
}

describe("ACP model and thought level controls", () => {
  let catalog: AgentCatalog;
  beforeEach(() => {
    vi.resetAllMocks();
    catalog = fixture();
    useAgentRegistryStore.setState({
      catalog: null,
      pending: null,
      error: null,
      message: null,
      revision: 0,
    });
    vi.mocked(getAgentCatalog).mockImplementation(async () => catalog);
  });

  it("uses the agent's values and refreshes the confirmed reasoning selection", async () => {
    vi.mocked(selectAgentThoughtLevel).mockImplementation(
      async (_id, value) => {
        catalog = fixture();
        catalog.agents[0].status.thought_level!.current = value;
        return catalog.agents[0].status;
      },
    );
    render(<Harness />);
    const select = await screen.findByRole("combobox", {
      name: "Synthetic Agentの推論量",
    });
    expect(select).toHaveValue("deliberate");
    expect(screen.getByRole("option", { name: "Quick" })).toHaveValue("brief");
    fireEvent.change(select, { target: { value: "brief" } });
    expect(selectAgentThoughtLevel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "保存" })).toBeDisabled(),
    );
    expect(select).toHaveValue("brief");
    expect(selectAgentThoughtLevel).toHaveBeenCalledWith("synthetic", "brief");
    expect(selectAgentModel).not.toHaveBeenCalled();
  });

  it("removes the reasoning control when the changed model no longer supports it", async () => {
    vi.mocked(selectAgentModel).mockImplementation(async () => {
      catalog = fixture();
      catalog.agents[0].status.model!.current = "fixed";
      catalog.agents[0].status.thought_level = null;
      return catalog.agents[0].status;
    });
    render(<Harness />);
    await screen.findByRole("combobox", { name: "Synthetic Agentの推論量" });
    fireEvent.change(
      screen.getByRole("combobox", { name: "Synthetic Agentのモデル" }),
      { target: { value: "fixed" } },
    );
    expect(selectAgentModel).not.toHaveBeenCalled();
    expect(
      screen.getByRole("combobox", { name: "Synthetic Agentの推論量" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("combobox", { name: "Synthetic Agentの推論量" }),
      ).not.toBeInTheDocument(),
    );
    expect(selectAgentThoughtLevel).not.toHaveBeenCalled();
  });

  it("preserves a rejected draft for retry and restores the confirmed value on discard", async () => {
    vi.mocked(selectAgentThoughtLevel).mockRejectedValue(
      new Error("選択した推論量を利用できません。"),
    );
    render(<Harness />);
    const select = await screen.findByRole("combobox", {
      name: "Synthetic Agentの推論量",
    });
    fireEvent.change(select, { target: { value: "brief" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(
      await screen.findByText("選択した推論量を利用できません。"),
    ).toBeVisible();
    expect(select).toHaveValue("brief");
    expect(screen.getByRole("button", { name: "保存" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "破棄" }));
    expect(select).toHaveValue("deliberate");
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });

  it("retries only the remaining agent draft after a partial save failure", async () => {
    catalog.agents.push({
      ...structuredClone(catalog.agents[0]),
      id: "second",
      name: "Second Agent",
    });
    useAgentRegistryStore.setState({ catalog });
    let rejectSecond = true;
    vi.mocked(selectAgentModel).mockImplementation(async (id, model) => {
      if (id === "second" && rejectSecond)
        throw new Error("Second Agentに接続できません。");
      const agent = catalog.agents.find((item) => item.id === id)!;
      agent.status.model!.current = model;
      return agent.status;
    });
    const { result } = renderHook(() => useAgentModelDrafts(false));
    act(() => {
      result.current.change("synthetic", "model", "fixed");
      result.current.change("second", "model", "fixed");
    });
    await act(async () => {
      expect(await result.current.save()).toBe(false);
    });
    expect(result.current.drafts).toEqual({ second: { model: "fixed" } });
    expect(result.current.error).toBe("Second Agentに接続できません。");
    rejectSecond = false;
    await act(async () => {
      expect(await result.current.save()).toBe(true);
    });
    expect(result.current.dirty).toBe(false);
    expect(vi.mocked(selectAgentModel).mock.calls).toEqual([
      ["synthetic", "fixed"],
      ["second", "fixed"],
      ["second", "fixed"],
    ]);
  });

  it("clears unsaved model changes when a meeting starts", async () => {
    const view = render(<Harness />);
    fireEvent.change(
      await screen.findByRole("combobox", { name: "Synthetic Agentのモデル" }),
      { target: { value: "fixed" } },
    );
    view.rerender(<Harness locked />);
    expect(
      screen.getByRole("combobox", { name: "Synthetic Agentのモデル" }),
    ).toHaveValue("flexible");
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    expect(selectAgentModel).not.toHaveBeenCalled();
  });

  it("locks model and reasoning changes during a meeting", async () => {
    render(<Harness locked />);
    expect(
      await screen.findByRole("combobox", { name: "Synthetic Agentの推論量" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("combobox", { name: "Synthetic Agentのモデル" }),
    ).toBeDisabled();
  });

  it("does not offer reasoning changes for disconnected or unsupported agents", async () => {
    catalog.agents[0].status.ready = false;
    render(<Harness />);
    expect(
      await screen.findByRole("combobox", { name: "Synthetic Agentのモデル" }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("combobox", { name: "Synthetic Agentの推論量" }),
    ).not.toBeInTheDocument();
  });
});
