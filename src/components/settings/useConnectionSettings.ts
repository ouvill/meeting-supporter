import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import { testConnectionApiSettingsConnectionsTestPost } from "../../api/generated/sdk.gen";
import {
  CONNECTIONS,
  type ConnectionProvider,
  type ConnectionSecretKey,
  type ConnectionVerification,
} from "./ApiConnectionControl";
import { CONNECTION_PROVIDER_BY_STT } from "./settingsValidation";
import type {
  ConnectionUiState,
  SettingsCategory,
  SettingsFieldErrors,
  SettingsForm,
} from "./types";

const INITIAL_CONNECTION_VERIFICATION: Record<
  ConnectionProvider,
  ConnectionVerification
> = {
  openai: "unverified",
  deepgram: "unverified",
  xai: "unverified",
  gemini: "unverified",
  anthropic: "unverified",
};

type SectionError = {
  category: SettingsCategory;
  message: string;
} | null;

interface UseConnectionSettingsOptions {
  form: SettingsForm;
  setForm: Dispatch<SetStateAction<SettingsForm>>;
  savedBaseline: SettingsForm | null;
  audioSettingsLocked: boolean;
  setFieldErrors: Dispatch<SetStateAction<SettingsFieldErrors>>;
  setSectionError: Dispatch<SetStateAction<SectionError>>;
  setSaveMessage: Dispatch<SetStateAction<string | null>>;
}

export function useConnectionSettings({
  form,
  setForm,
  savedBaseline,
  audioSettingsLocked,
  setFieldErrors,
  setSectionError,
  setSaveMessage,
}: UseConnectionSettingsOptions) {
  const [connectionEditingProvider, setConnectionEditingProvider] =
    useState<ConnectionProvider | null>(null);
  const [pendingDeleteSecrets, setPendingDeleteSecrets] = useState<
    ConnectionSecretKey[]
  >([]);
  const [connectionVerification, setConnectionVerification] = useState(
    INITIAL_CONNECTION_VERIFICATION,
  );
  const [connectionTestingProvider, setConnectionTestingProvider] =
    useState<ConnectionProvider | null>(null);
  const [connectionTestMessages, setConnectionTestMessages] = useState<
    Partial<Record<ConnectionProvider, string>>
  >({});
  const previousAudioSettingsLocked = useRef(audioSettingsLocked);

  useEffect(() => {
    if (!audioSettingsLocked) {
      previousAudioSettingsLocked.current = false;
      return;
    }
    if (previousAudioSettingsLocked.current || savedBaseline === null) return;

    previousAudioSettingsLocked.current = true;
    const activeProvider =
      CONNECTION_PROVIDER_BY_STT[savedBaseline.sttBackend] ?? null;
    if (activeProvider === null) return;

    const activeSecretKey = CONNECTIONS[activeProvider].secretKey;
    setForm((previous) => {
      const secretInputs = { ...previous.secretInputs };
      if (
        Object.prototype.hasOwnProperty.call(
          savedBaseline.secretInputs,
          activeSecretKey,
        )
      ) {
        secretInputs[activeSecretKey] =
          savedBaseline.secretInputs[activeSecretKey];
      } else {
        delete secretInputs[activeSecretKey];
      }
      return { ...previous, secretInputs };
    });
    setPendingDeleteSecrets((previous) =>
      previous.filter((secretKey) => secretKey !== activeSecretKey),
    );
    setConnectionEditingProvider((previous) =>
      previous === activeProvider ? null : previous,
    );
    setConnectionVerification((previous) => ({
      ...previous,
      [activeProvider]: "unverified",
    }));
    setConnectionTestMessages((previous) => {
      const next = { ...previous };
      delete next[activeProvider];
      return next;
    });
  }, [audioSettingsLocked, savedBaseline, setForm]);

  const connectionStates = useMemo(
    () =>
      (Object.keys(CONNECTIONS) as ConnectionProvider[]).reduce<
        Record<ConnectionProvider, ConnectionUiState>
      >(
        (states, provider) => {
          const secretKey = CONNECTIONS[provider].secretKey;
          states[provider] = pendingDeleteSecrets.includes(secretKey)
            ? "pending-delete"
            : connectionVerification[provider] === "failed"
              ? "failed"
              : connectionVerification[provider] === "verified"
                ? "verified"
                : form.secretInputs[secretKey]?.trim()
                  ? "draft-unverified"
                  : form.secretsStatus[secretKey]
                    ? "saved-unverified"
                    : "unconfigured";
          return states;
        },
        {} as Record<ConnectionProvider, ConnectionUiState>,
      ),
    [
      connectionVerification,
      form.secretInputs,
      form.secretsStatus,
      pendingDeleteSecrets,
    ],
  );

  const clearConnectionTestMessage = (provider: ConnectionProvider) => {
    setConnectionTestMessages((previous) => {
      const next = { ...previous };
      delete next[provider];
      return next;
    });
  };

  const beginConnectionEdit = (provider: ConnectionProvider) => {
    setConnectionEditingProvider(provider);
  };

  const cancelConnectionEdit = (provider: ConnectionProvider) => {
    const key = CONNECTIONS[provider].secretKey;
    setForm((previous) => ({
      ...previous,
      secretInputs: { ...previous.secretInputs, [key]: "" },
    }));
    setConnectionEditingProvider((previous) =>
      previous === provider ? null : previous,
    );
    setConnectionVerification((previous) => ({
      ...previous,
      [provider]: "unverified",
    }));
    clearConnectionTestMessage(provider);
  };

  const updateSecret = (provider: ConnectionProvider, value: string): void => {
    const key = CONNECTIONS[provider].secretKey;
    setForm((previous) => ({
      ...previous,
      secretInputs: { ...previous.secretInputs, [key]: value },
    }));
    setConnectionVerification((previous) => ({
      ...previous,
      [provider]: "unverified",
    }));
    clearConnectionTestMessage(provider);
    setPendingDeleteSecrets((previous) =>
      previous.filter((secret) => secret !== key),
    );
    setFieldErrors((previous) => ({
      ...previous,
      audio: undefined,
      support: undefined,
      advanced: undefined,
    }));
    setSectionError(null);
    setSaveMessage(null);
  };

  const testConnection = async (
    provider: ConnectionProvider,
  ): Promise<void> => {
    if (connectionTestingProvider !== null) return;
    const secretKey = CONNECTIONS[provider].secretKey;
    const draftKey = form.secretInputs[secretKey]?.trim();
    if (!form.secretsStatus[secretKey] && !draftKey) return;
    setConnectionTestingProvider(provider);
    clearConnectionTestMessage(provider);
    try {
      const { data, error } =
        await testConnectionApiSettingsConnectionsTestPost({
          body: {
            provider,
            ...(draftKey ? { api_key: draftKey } : {}),
          },
        });
      const verified = Boolean(
        data?.ok && data.status === "verified" && !error,
      );
      setConnectionVerification((previous) => ({
        ...previous,
        [provider]: verified ? "verified" : "failed",
      }));
      setConnectionTestMessages((previous) => ({
        ...previous,
        [provider]:
          data?.message ??
          (verified ? "接続を確認しました。" : "接続を確認できませんでした。"),
      }));
    } catch {
      setConnectionVerification((previous) => ({
        ...previous,
        [provider]: "failed",
      }));
      setConnectionTestMessages((previous) => ({
        ...previous,
        [provider]:
          "接続を確認できませんでした。APIキーとネットワークを確認してください。",
      }));
    } finally {
      setConnectionTestingProvider(null);
    }
  };

  const scheduleSecretDeletion = (provider: ConnectionProvider) => {
    const key = CONNECTIONS[provider].secretKey;
    setPendingDeleteSecrets((previous) =>
      previous.includes(key) ? previous : [...previous, key],
    );
    setForm((previous) => ({
      ...previous,
      secretInputs: { ...previous.secretInputs, [key]: "" },
    }));
    setConnectionVerification((previous) => ({
      ...previous,
      [provider]: "unverified",
    }));
    setConnectionEditingProvider(null);
    clearConnectionTestMessage(provider);
  };

  const cancelSecretDeletion = (provider: ConnectionProvider) => {
    const key = CONNECTIONS[provider].secretKey;
    setPendingDeleteSecrets((previous) =>
      previous.filter((secret) => secret !== key),
    );
  };

  const resetAfterSave = () => {
    setPendingDeleteSecrets([]);
    setConnectionEditingProvider(null);
    setConnectionTestMessages({});
  };

  const resetAfterDiscard = () => {
    setPendingDeleteSecrets([]);
    setConnectionEditingProvider(null);
    setConnectionTestMessages({});
    setConnectionVerification(INITIAL_CONNECTION_VERIFICATION);
  };

  return {
    connectionEditingProvider,
    connectionTestingProvider,
    connectionTestMessages,
    pendingDeleteSecrets,
    connectionStates,
    updateSecret,
    beginConnectionEdit,
    cancelConnectionEdit,
    testConnection,
    scheduleSecretDeletion,
    cancelSecretDeletion,
    resetAfterSave,
    resetAfterDiscard,
  };
}
