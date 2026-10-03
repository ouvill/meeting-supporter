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
  if (!["whisper", "reazonspeech", "dummy"].includes(form.sttBackend)) {
    errors.audio = "音声認識方式を選択してください。";
  }
  if (
    [
      form.openaiModel,
      form.geminiModel,
      form.anthropicModel,
      form.ollamaModel,
    ].some((model) => !model.trim() || model.length > 256)
  ) {
    errors.advanced = "各AIサービスのモデル識別子を入力してください。";
  }
  return errors;
}

export function firstSettingsErrorCategory(
  errors: SettingsFieldErrors,
): SettingsCategory {
  if (errors.support) return "support";
  if (errors.audio || errors.advanced) return "support";
  return "privacy";
}
