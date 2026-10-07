import { useState } from "react";
import { client } from "../api/generated/client.gen";
import { useAiRoutes } from "../hooks/useAiRoutes";
import { useMeetingStore } from "../store/meetingStore";
import type { SendFn, SocketState } from "../types";
import { AppFrame } from "./product/AppFrame";
import { SettingsModal } from "./SettingsModal";
import { TooltipProvider } from "./ui/Tooltip";

function route(
  id: string,
  label: string,
  kind: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    id,
    kind,
    label,
    description: "",
    availability: "available",
    readiness: "ready",
    selectable: true,
    selected: false,
    data_location: kind === "local" ? "local" : "external",
    billing_owner: kind === "local" ? "none" : "user",
    capabilities: ["reply"],
    reason_code: null,
    message: "",
    action: "none",
    ...overrides,
  };
}

const AGENT_STATUS = {
  ready: true,
  message: "",
  auth_methods: [],
  model: {
    current: "fast",
    options: [
      { id: "fast", name: "Fast" },
      { id: "accurate", name: "Accurate" },
    ],
  },
  thought_level: {
    current: "medium",
    options: [
      { id: "low", name: "低" },
      { id: "medium", name: "中" },
      { id: "high", name: "高" },
    ],
  },
};

const FIXTURES: Partial<Record<string, (url: URL) => unknown>> = {
  "/api/settings": () => ({
    ollama: { base_url: "http://127.0.0.1:11434/v1" },
    stt: { backend: "whisper", language: "ja" },
    reply: {
      enabled: true,
      auto_generate: false,
      default_style: "standard",
      styles: [{ id: "standard", label: "標準", enabled: true, priority: 10 }],
    },
    secrets: { OPENAI_API_KEY: true },
    providers: [],
    data_dir: "~/meeting-supporter/data",
    context_dir: "~/meeting-supporter/context",
    usage: {
      budget: {},
      current_meeting: {},
      current_month: {},
      billing_mode: "external_subscription",
    },
    recording_retention: {},
  }),
  "/api/ai/routes": () => ({
    routes: [
      route("openai", "OpenAI", "byok", { selected: true }),
      route("gemini", "Google Gemini", "byok", {
        readiness: "setup_required",
        message: "APIキーを設定してください。",
      }),
      route("anthropic", "Anthropic", "byok", {
        readiness: "setup_required",
        message: "APIキーを設定してください。",
      }),
      route("ollama", "Ollama", "local"),
      route("acp:sample-agent", "Sample Agent", "agent", {
        billing_owner: "external_subscription",
      }),
    ],
    assignments: { reply: "openai" },
  }),
  "/api/stt/model": () => ({
    backend: "whisper",
    model_id: "large-v3-turbo",
    state: "ready",
    phase: "idle",
    language: "ja",
    downloaded_bytes: 0,
    total_bytes: null,
    progress_percent: null,
    model_path: null,
    storage_path: "~/meeting-supporter/speech",
    error_code: null,
    message: "",
    retryable: true,
    cancelable: false,
  }),
  "/api/stt/capabilities": () => ({ whisper_gpu: false }),
  "/api/settings/ai/models": (url) => ({
    ok: true,
    provider: url.searchParams.get("provider"),
    models: [
      { id: "preview-model-mini", label: "推奨モデル" },
      { id: "preview-model-large", label: "高精度モデル" },
    ],
    message: null,
  }),
  "/api/settings/ollama/models": (url) => ({
    ok: true,
    base_url: url.searchParams.get("base_url"),
    models: ["qwen3", "synthetic-model:8b"],
    message: null,
  }),
  "/api/ai/agents": () => ({
    supported: true,
    agents: [
      {
        id: "sample-agent",
        name: "Sample Agent",
        description: "プレビュー用のエージェントです。",
        authors: ["Preview"],
        version: "1.0.0",
        installed_version: "1.0.0",
        update_version: null,
        supported: true,
        distribution: "npm",
        status: AGENT_STATUS,
      },
      {
        id: "another-agent",
        name: "Another Agent",
        description: "未導入のエージェントです。",
        authors: ["Preview"],
        version: "2.1.0",
        installed_version: null,
        update_version: null,
        supported: true,
        distribution: "binary",
        status: { ready: false, message: "", auth_methods: [] },
      },
    ],
    update_count: 0,
    checked_at: null,
    update_message: null,
  }),
};

const previewFetch: typeof fetch = async (input) => {
  const url = new URL(input instanceof Request ? input.url : String(input));
  const fixture = FIXTURES[url.pathname];
  return new Response(JSON.stringify(fixture ? fixture(url) : {}), {
    status: fixture ? 200 : 404,
    headers: { "Content-Type": "application/json" },
  });
};

client.setConfig({ baseUrl: "http://preview.invalid", fetch: previewFetch });

function createPreviewState(): SocketState {
  return {
    ...useMeetingStore.getState(),
    connected: true,
    devices: [
      { index: 1, name: "会議アプリの音声", is_monitor: true },
      { index: 2, name: "MacBookのマイク", is_monitor: false },
    ],
    deviceOther: 1,
    deviceSelf: 2,
    levelOther: 0.16,
    levelSelf: 0.08,
  };
}

export function SettingsPreview() {
  const [state] = useState<SocketState>(createPreviewState);
  const routes = useAiRoutes();
  const send: SendFn = () => undefined;

  return (
    <TooltipProvider>
      <div className="flex h-screen flex-col overflow-hidden bg-paper">
        <AppFrame
          active="home"
          connected
          onNavigate={() => undefined}
          onSettings={() => undefined}
          settings={
            <SettingsModal
              onClose={() => undefined}
              routes={routes}
              state={state}
              send={send}
            />
          }
        >
          {null}
        </AppFrame>
      </div>
    </TooltipProvider>
  );
}
