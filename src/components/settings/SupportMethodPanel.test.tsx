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

function route(overrides: Partial<AiRouteReadModel>): AiRouteReadModel {
  return {
    id: "codex",
    kind: "subscription_app",
    label: "Codex",
    description: "ChatGPT subscription",
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
    info: null,
    minutes: null,
    ...assignmentOverrides,
  };
  render(
    <SupportMethodPanel
      routes={routes}
      assignments={assignments}
      loading={options.loading ?? false}
      manualReloadStatus={options.manualReloadStatus ?? "idle"}
      error={options.error}
      replyEnabled={options.replyEnabled ?? true}
      replyAutoGenerate={options.replyAutoGenerate ?? false}
      connectionStates={{
        openai: "unconfigured",
        deepgram: "unconfigured",
        xai: "unconfigured",
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
      onRouteAction={vi.fn()}
      onReload={onReload}
    />,
  );
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
    ["user", "利用者"],
    ["none", "外部サービス料金なし"],
    ["invalid", "確認できません"],
  ])("maps billing owner %s without guessing", (value, label) => {
    expect(billingOwnerLabel(value)).toBe(label);
  });
});

describe("SupportMethodPanel", () => {
  it("separates general routes from routes requiring setup without hiding any route", () => {
    renderPanel(
      [
        route({
          id: "managed",
          kind: "managed",
          label: "Managed",
          description: "managed",
          readiness: "not_offered",
          selectable: false,
        }),
        route({
          id: "codex",
          kind: "subscription_app",
          label: "Codex",
          description: "subscription",
        }),
        route({
          id: "gemini",
          kind: "byok",
          label: "Gemini API",
          description: "BYOK",
          readiness: "setup_required",
        }),
        route({
          id: "ollama",
          kind: "local",
          label: "Ollama",
          description: "local",
          readiness: "setup_required",
        }),
        route({
          id: "acp",
          kind: "local",
          label: "ACP",
          description: "agent",
          readiness: "setup_required",
        }),
      ],
      { reply: "gemini" },
    );
    expect(screen.getByRole("heading", { name: "一般" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "要設定" })).toBeInTheDocument();
    expect(screen.getByText("アプリにおまかせ")).toBeInTheDocument();
    expect(screen.getByText("ChatGPT の契約を使う")).toBeInTheDocument();
    expect(screen.getByText("Gemini API")).toBeInTheDocument();
    expect(screen.getByText("Ollama")).toBeInTheDocument();
    expect(screen.getByText("ACP")).toBeInTheDocument();
    expect(
      screen
        .getAllByRole("button", { name: "返答案" })
        .some((button) => button.getAttribute("aria-pressed") === "true"),
    ).toBe(true);
  });

  it("assigns and clears three supported use cases independently on one route card", () => {
    const { onAssignmentChange } = renderPanel(
      [
        route({
          capabilities: ["reply", "info", "minutes", "stream", "cancel"],
        }),
      ],
      { reply: "codex", info: "codex" },
    );

    const reply = screen.getByRole("button", { name: "返答案" });
    const info = screen.getByRole("button", { name: "会話メモ" });
    const minutes = screen.getByRole("button", { name: "要約・議事録" });
    expect(reply).toHaveAttribute("aria-pressed", "true");
    expect(info).toHaveAttribute("aria-pressed", "true");
    expect(minutes).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("button", { name: "stream" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "cancel" })).not.toBeInTheDocument();
    expect(document.querySelectorAll('[data-route-id="codex"]')).toHaveLength(1);
    expect(screen.getAllByText("処理場所")).toHaveLength(1);

    fireEvent.click(reply);
    fireEvent.click(minutes);

    expect(onAssignmentChange).toHaveBeenNthCalledWith(1, "reply", null);
    expect(onAssignmentChange).toHaveBeenNthCalledWith(
      2,
      "minutes",
      "codex",
    );
  });

  it("displays processing location and billing responsibility on each route card", () => {
    renderPanel([route({ data_location: "local", billing_owner: "none" })]);

    expect(screen.getByText("処理場所")).toBeInTheDocument();
    expect(screen.getByText("このPC")).toBeInTheDocument();
    expect(screen.getByText("費用負担")).toBeInTheDocument();
    expect(screen.getByText("外部サービス料金なし")).toBeInTheDocument();
  });

  it("renders provider-specific API controls in known BYOK route cards", () => {
    renderPanel([
      route({
        id: "gemini",
        kind: "byok",
        label: "Gemini API",
        description: "Gemini BYOK",
        readiness: "setup_required",
      }),
      route({
        id: "openai",
        kind: "byok",
        label: "OpenAI API",
        description: "OpenAI BYOK",
        readiness: "setup_required",
      }),
      route({
        id: "anthropic",
        kind: "byok",
        label: "Anthropic API",
        description: "Anthropic BYOK",
        readiness: "setup_required",
      }),
    ]);

    expect(screen.getByLabelText("Google Gemini APIキー")).toHaveAttribute(
      "type",
      "password",
    );
    expect(screen.getByLabelText("OpenAI APIキー")).toHaveAttribute(
      "type",
      "password",
    );
    expect(screen.getByLabelText("Anthropic APIキー")).toHaveAttribute(
      "type",
      "password",
    );
    expect(
      screen.getByText("APIキーが必要な方法は、各カード内で設定できます。"),
    ).toBeInTheDocument();
  });

  it("does not infer a credential provider for an unknown BYOK route", () => {
    renderPanel([
      route({
        id: "unknown-byok",
        kind: "byok",
        label: "Unknown API",
        description: "Unknown BYOK",
        readiness: "setup_required",
      }),
    ]);

    expect(screen.getByText("Unknown API")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Unknown.*APIキー/)).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /API接続/ })).not.toBeInTheDocument();
  });

  it("allows assigning a BYOK route before its credential is configured", () => {
    const { onAssignmentChange } = renderPanel([
      route({
        id: "openai",
        kind: "byok",
        label: "OpenAI API",
        description: "BYOK",
        readiness: "setup_required",
      }),
    ]);

    fireEvent.click(screen.getByRole("button", { name: "返答案" }));
    expect(onAssignmentChange).toHaveBeenCalledWith("reply", "openai");
  });
  it("keeps the selected route and reply toggles visible while a manual refresh is in progress", () => {
    const { onReload } = renderPanel(
      [
        route({
          id: "gemini",
          kind: "byok",
          label: "Gemini API",
          description: "BYOK",
          readiness: "setup_required",
        }),
      ],
      { reply: "gemini" },
      {
        loading: true,
        manualReloadStatus: "loading",
        replyEnabled: true,
        replyAutoGenerate: true,
      },
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "状態を更新しています",
    );
    expect(screen.getByText("Gemini API")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返答案" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByRole("checkbox", { name: "返答案を表示する" }),
    ).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "発話ごとに自動で作る" }),
    ).toBeChecked();
    expect(screen.getByRole("button", { name: "状態を再確認" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "状態を再確認" }));
    expect(onReload).not.toHaveBeenCalled();
  });
  it("announces manual refresh failure while retaining the current route card", () => {
    renderPanel(
      [
        route({
          id: "gemini",
          kind: "byok",
          label: "Gemini API",
          description: "BYOK",
          readiness: "setup_required",
        }),
      ],
      { reply: "gemini" },
      {
        manualReloadStatus: "error",
        error: "支援方法の状態を確認できませんでした。",
      },
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "更新できませんでした",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "支援方法の状態を確認できませんでした。",
    );
    expect(screen.getByText("Gemini API")).toBeInTheDocument();
  });

  it("announces manual refresh success without removing the current route card", () => {
    renderPanel(
      [
        route({
          id: "gemini",
          kind: "byok",
          label: "Gemini API",
          description: "BYOK",
          readiness: "setup_required",
        }),
      ],
      { reply: "gemini" },
      { manualReloadStatus: "success" },
    );

    expect(screen.getByRole("status")).toHaveTextContent("状態を更新しました");
    expect(screen.getByText("Gemini API")).toBeInTheDocument();
  });
});
