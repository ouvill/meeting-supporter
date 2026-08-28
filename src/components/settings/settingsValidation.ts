import type { AiRouteReadModel } from "../../hooks/useAiRoutes";
import {
  CONNECTION_PROVIDER_BY_ROUTE,
  type ConnectionProvider,
} from "./ApiConnectionControl";
import {
  isConnectionUsable,
  type ConnectionUiState,
  type SettingsCategory,
  type SettingsFieldErrors,
  type SettingsForm,
} from "./types";

export const CONNECTION_PROVIDER_BY_STT: Record<string, ConnectionProvider> = {
  deepgram: "deepgram",
  openai: "openai",
  xai: "xai",
};

export function validateSettingsForm(
  form: SettingsForm,
  routes: AiRouteReadModel[],
  connectionStates: Record<ConnectionProvider, ConnectionUiState>,
): SettingsFieldErrors {
  const errors: SettingsFieldErrors = {};
  const routeWithMissingCredential = routes.find((route) => {
    if (route.kind !== "byok") return false;
    const provider =
      CONNECTION_PROVIDER_BY_ROUTE[
        route.id as keyof typeof CONNECTION_PROVIDER_BY_ROUTE
      ];
    return provider ? !isConnectionUsable(connectionStates[provider]) : false;
  });
  if (routeWithMissingCredential) {
    errors.support =
      "この支援方法を利用するには、利用可能なAPIキーが必要です。";
  }
  if (routes.some((route) => route.id === "acp") && !form.acpCommand.trim()) {
    errors.advanced = "ACPを利用するには、起動commandを入力してください。";
  }
  const sttProvider = CONNECTION_PROVIDER_BY_STT[form.sttBackend];
  if (sttProvider && !isConnectionUsable(connectionStates[sttProvider])) {
    errors.audio =
      "クラウド音声認識を利用するには、利用可能なAPIキーが必要です。";
  }
  return errors;
}

export function firstSettingsErrorCategory(
  errors: SettingsFieldErrors,
): SettingsCategory {
  if (errors.support) return "support";
  if (errors.audio) return "audio";
  if (errors.advanced) return "advanced";
  return "privacy";
}
