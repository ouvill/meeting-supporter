import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type {
  AiRouteDraftAssignments,
  AiRouteReadModel,
  AiRoutesReloadStatus,
} from "../../hooks/useAiRoutes";
import {
  billingOwnerLabel,
  dataLocationLabel,
  SupportMethodPanel,
} from "./SupportMethodPanel";

vi.mock("./AgentRegistryPanel", () => ({ AgentRegistryPanel: () => null }));

function route(overrides: Partial<AiRouteReadModel>): AiRouteReadModel {
  return {
    id: "ollama",
    kind: "local",
    label: "Ollama",
    description: "Local model",
    availability: "experimental",
    readiness: "ready",
    selectable: true,
    selected: false,
    data_location: "external",
    billing_owner: "external_subscription",
    capabilities: ["reply"],
    reason_code: null,
    message: "",
    action: "none",
    ...overrides,
  };
}

function renderPanel(
  routes: AiRouteReadModel[],
  assignmentOverrides: Partial<AiRouteDraftAssignments> = {},
  options: {
    loading?: boolean;
    manualReloadStatus?: AiRoutesReloadStatus;
    error?: string;
    replyEnabled?: boolean;
    replyAutoGenerate?: boolean;
  } = {},
) {
  const onReload = vi.fn();
  const onAssignmentChange = vi.fn();
  const assignments: AiRouteDraftAssignments = {
    reply: null,
    ...assignmentOverrides,
  };
  function Harness() {
    const [connectionRouteId, setConnectionRouteId] = useState<string | null>(
      null,
    );
    return (
      <SupportMethodPanel
        connectionRouteId={connectionRouteId}
        onConnectionRouteChange={setConnectionRouteId}
        routes={routes}
        assignments={assignments}
        loading={options.loading ?? false}
        manualReloadStatus={options.manualReloadStatus ?? "idle"}
        error={options.error}
        replyEnabled={options.replyEnabled ?? true}
        replyAutoGenerate={options.replyAutoGenerate ?? false}
        connectionStates={{
          openai: "unconfigured",
          gemini: "unconfigured",
          anthropic: "unconfigured",
        }}
        secretsStatus={{}}
        secretInputs={{}}
        connectionEditingProvider={null}
        connectionTestingProvider={null}
        connectionTestMessages={{}}
        onBeginConnectionEdit={vi.fn()}
        onCancelConnectionEdit={vi.fn()}
        onSecretChange={vi.fn()}
        onTestConnection={vi.fn()}
        onRequestSecretDelete={vi.fn()}
        onCancelSecretDelete={vi.fn()}
        onAssignmentChange={onAssignmentChange}
        onReplyEnabledChange={vi.fn()}
        onReplyAutoGenerateChange={vi.fn()}
        onReload={onReload}
      />
    );
  }
  render(<Harness />);
  return { onReload, onAssignmentChange };
}

describe("route metadata labels", () => {
  it.each([
    ["local", "このPC"],
    ["cloud", "クラウド"],
    ["external", "外部サービス"],
    ["invalid", "確認できません"],
  ])("maps data location %s without guessing", (value, label) => {
    expect(dataLocationLabel(value)).toBe(label);
  });

  it.each([
    ["app", "提供時に料金をご案内（無料ではありません）"],
    ["external_subscription", "利用者の外部契約"],
    ["user", "利用するサービスの従量料金"],
    ["none", "外部サービス料金なし"],
    ["invalid", "確認できません"],
  ])("maps billing owner %s without guessing", (value, label) => {
    expect(billingOwnerLabel(value)).toBe(label);
  });
});

describe("SupportMethodPanel", () => {
  it("shows only the selected service's connection while retaining other choices", () => {
    renderPanel(
      [
        route({ id: "openai", kind: "byok", label: "OpenAI" }),
        route({ id: "gemini", kind: "byok", label: "Gemini" }),
      ],
      { reply: "openai" },
    );
    // An unconfigured key is asked for directly, without a toggle to find.
    expect(screen.getByLabelText("OpenAI APIキー")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "接続設定" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Google Gemini APIキー"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Gemini" })).toBeInTheDocument();
  });
  it("assigns a service before credentials are configured", () => {
    const { onAssignmentChange } = renderPanel([
      route({ id: "openai", kind: "byok", readiness: "setup_required" }),
    ]);
    fireEvent.change(screen.getByRole("combobox", { name: "返答案に使うAI" }), {
      target: { value: "openai" },
    });
    expect(onAssignmentChange).toHaveBeenCalledWith("reply", "openai");
  });
  it("omits hosted account services even when the backend marks them ready", () => {
    renderPanel([
      route({
        id: "managed",
        kind: "managed",
        label: "Hosted",
        action: "subscribe",
      }),
      route({}),
    ]);
    expect(
      screen.queryByRole("option", { name: /Hosted/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /ログイン|月額|支払い/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Ollama" })).toBeEnabled();
  });
  it("keeps unavailable choices disabled without inferring credentials for unknown services", () => {
    renderPanel(
      [
        route({
          id: "unknown",
          kind: "byok",
          label: "Unknown API",
          selectable: false,
          readiness: "unavailable",
        }),
      ],
      { reply: "unknown" },
    );
    expect(screen.getByRole("option", { name: /Unknown API/ })).toBeDisabled();
    expect(
      screen.queryByRole("textbox", { name: /APIキー/ }),
    ).not.toBeInTheDocument();
  });
  it("shows processing location and cost for the selected service", () => {
    renderPanel([route({ data_location: "local", billing_owner: "none" })], {
      reply: "ollama",
    });
    fireEvent.click(screen.getByText("データの送信先と費用"));
    expect(screen.getByText("このPC")).toBeVisible();
    expect(screen.getByText("外部サービス料金なし")).toBeVisible();
    expect(screen.getByText("試験提供")).toBeVisible();
  });
  it("offers a retry at the failed status without discarding the selected AI", () => {
    const { onReload } = renderPanel(
      [route({})],
      { reply: "ollama" },
      {
        manualReloadStatus: "error",
        error: "状態を取得できませんでした。",
      },
    );
    expect(
      screen.getByRole("combobox", { name: "返答案に使うAI" }),
    ).toHaveValue("ollama");
    fireEvent.click(screen.getByRole("button", { name: "再試行" }));
    expect(onReload).toHaveBeenCalledOnce();
  });
  it("does not show a routine reload action or success notification", () => {
    renderPanel(
      [route({})],
      { reply: "ollama" },
      { manualReloadStatus: "success" },
    );
    expect(
      screen.queryByRole("button", { name: /再確認|再試行/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("状態を更新しました")).not.toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "自動で返答案を作る" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "AIエージェントを追加" }),
    ).toBeVisible();
  });
});
