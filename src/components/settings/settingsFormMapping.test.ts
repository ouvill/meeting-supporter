import { describe, expect, it } from "vitest";

import {
  INITIAL_SETTINGS_FORM,
  mapSettingsResponseToForm,
  type SettingsResponseWithRetention,
} from "./settingsFormMapping";

describe("settingsFormMapping STT defaults", () => {
  it("uses ReazonSpeech for the initial form", () => {
    expect(INITIAL_SETTINGS_FORM.sttBackend).toBe("reazonspeech");
    expect(INITIAL_SETTINGS_FORM.sttLang).toBe("ja");
  });

  it("uses ReazonSpeech when persisted settings omit the STT backend", () => {
    const settings = {
      secrets: {},
      agents: { info_enabled: true },
    } as unknown as SettingsResponseWithRetention;

    const form = mapSettingsResponseToForm(settings);

    expect(form.sttBackend).toBe("reazonspeech");
    expect(form.sttLang).toBe("ja");
  });
});
