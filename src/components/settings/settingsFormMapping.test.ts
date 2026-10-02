import { describe, expect, it } from "vitest";

import {
  INITIAL_SETTINGS_FORM,
  mapSettingsResponseToForm,
  mapSettingsFormToPayload,
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
    } as unknown as SettingsResponseWithRetention;

    const form = mapSettingsResponseToForm(settings);

    expect(form.sttBackend).toBe("reazonspeech");
    expect(form.sttLang).toBe("ja");
  });
});

describe("settings save contract", () => {
  it("sends only supported audio settings to the Rust backend", () => {
    const form = {
      ...INITIAL_SETTINGS_FORM,
      sttBackend: "whisper",
      sttWhisperModel: "tiny",
      sttLang: "auto",
      sttDevice: "cpu",
    };
    const payload = mapSettingsFormToPayload(form, null, []);
    expect(payload.stt).toEqual({
      backend: "whisper",
      whisper_model: "tiny",
      language: "auto",
      device: "cpu",
      vad_engine: "silero",
      vad_sensitivity: 0.4,
      silence_duration: 0.8,
    });
  });
});
