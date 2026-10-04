import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getAgentCatalog,
  selectAgentModel,
  selectAgentThoughtLevel,
  type AgentCatalog,
} from "../../api/agentRegistry";
import { useAgentRegistryStore } from "../../store/agentRegistryStore";
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
    const onChanged = vi.fn();
    render(
      <AgentModelControl
        routeId="acp:synthetic"
        locked={false}
        onChanged={onChanged}
      />,
    );
    const select = await screen.findByRole("combobox", {
      name: "Synthetic Agentの推論量",
    });
    expect(select).toHaveValue("deliberate");
    expect(screen.getByRole("option", { name: "Quick" })).toHaveValue("brief");
    fireEvent.change(select, { target: { value: "brief" } });
    await waitFor(() => expect(select).toHaveValue("brief"));
    expect(selectAgentThoughtLevel).toHaveBeenCalledWith("synthetic", "brief");
    expect(onChanged).toHaveBeenCalledOnce();
    expect(selectAgentModel).not.toHaveBeenCalled();
  });

  it("removes the reasoning control when the changed model no longer supports it", async () => {
    vi.mocked(selectAgentModel).mockImplementation(async () => {
      catalog = fixture();
      catalog.agents[0].status.model!.current = "fixed";
      catalog.agents[0].status.thought_level = null;
      return catalog.agents[0].status;
    });
    render(
      <AgentModelControl
        routeId="acp:synthetic"
        locked={false}
        onChanged={vi.fn()}
      />,
    );
    await screen.findByRole("combobox", { name: "Synthetic Agentの推論量" });
    fireEvent.change(
      screen.getByRole("combobox", { name: "Synthetic Agentのモデル" }),
      { target: { value: "fixed" } },
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("combobox", { name: "Synthetic Agentの推論量" }),
      ).not.toBeInTheDocument(),
    );
    expect(selectAgentThoughtLevel).not.toHaveBeenCalled();
  });

  it("keeps the confirmed value and displays a rejected change", async () => {
    vi.mocked(selectAgentThoughtLevel).mockRejectedValue(
      new Error("選択した推論量を利用できません。"),
    );
    render(
      <AgentModelControl
        routeId="acp:synthetic"
        locked={false}
        onChanged={vi.fn()}
      />,
    );
    const select = await screen.findByRole("combobox", {
      name: "Synthetic Agentの推論量",
    });
    fireEvent.change(select, { target: { value: "brief" } });
    expect(
      await screen.findByText("選択した推論量を利用できません。"),
    ).toBeVisible();
    expect(select).toHaveValue("deliberate");
  });

  it("locks model and reasoning changes during a meeting", async () => {
    render(
      <AgentModelControl routeId="acp:synthetic" locked onChanged={vi.fn()} />,
    );
    expect(
      await screen.findByRole("combobox", { name: "Synthetic Agentの推論量" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("combobox", { name: "Synthetic Agentのモデル" }),
    ).toBeDisabled();
  });

  it("does not offer reasoning changes for disconnected or unsupported agents", async () => {
    catalog.agents[0].status.ready = false;
    render(
      <AgentModelControl
        routeId="acp:synthetic"
        locked={false}
        onChanged={vi.fn()}
      />,
    );
    expect(
      await screen.findByRole("combobox", { name: "Synthetic Agentのモデル" }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("combobox", { name: "Synthetic Agentの推論量" }),
    ).not.toBeInTheDocument();
  });
});
