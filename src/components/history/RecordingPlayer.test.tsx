import {
  act,
  cleanup,
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RecordingPlayer } from "./RecordingPlayer";
import type { RecordingAssetItem } from "../../api/generated/types.gen";

// ── Mock client baseUrl ───────────────────────────────────────────
const TEST_BASE_URL = "http://localhost:8000";

vi.mock("../../api/generated/client.gen", () => ({
  client: {
    getConfig: () => ({
      baseUrl: TEST_BASE_URL,
      headers: { Authorization: "Bearer test-token" },
    }),
  },
}));

// ── Fixture helpers ────────────────────────────────────────────────

function recordingAsset(
  overrides: Partial<RecordingAssetItem> & { role: "other" | "self" },
): RecordingAssetItem {
  const { role, ...rest } = overrides;
  return {
    id: `rec-${role}`,
    role,
    format: "wav",
    sample_rate: 16000,
    channels: 1,
    started_at: "2026-01-01T00:00:00Z",
    ended_at: "2026-06-01T00:00:00Z",
    size_bytes: null,
    ...rest,
  };
}

const bothRecordings: RecordingAssetItem[] = [
  recordingAsset({ role: "other" }),
  recordingAsset({ role: "self" }),
];

const onlyOtherRecording: RecordingAssetItem[] = [
  recordingAsset({ role: "other" }),
];

const onlySelfRecording: RecordingAssetItem[] = [
  recordingAsset({ role: "self" }),
];

// ── Media mock helpers ─────────────────────────────────────────────

/**
 * Set `duration` on rendered `<audio>` elements and fire `loadedmetadata`
 * so the component updates its duration state.
 */
function setupDuration(container: HTMLElement, durationSec: number): void {
  const audios = container.querySelectorAll<HTMLAudioElement>("audio");
  for (const audio of audios) {
    Object.defineProperty(audio, "duration", {
      value: durationSec,
      writable: true,
      configurable: true,
    });
    audio.dispatchEvent(new Event("loadedmetadata"));
  }
}

/**
 * Collect all rendered `<audio>` elements.
 */
function allAudios(container: HTMLElement): HTMLAudioElement[] {
  return Array.from(container.querySelectorAll<HTMLAudioElement>("audio"));
}

async function waitForAudioSources(container: HTMLElement): Promise<void> {
  await waitFor(() => {
    expect(
      allAudios(container).every((audio) =>
        audio.getAttribute("src")?.startsWith("blob:recording-"),
      ),
    ).toBe(true);
  });
}

// ── Spy helpers ──────────────────────────────────────────────────────
/**
 * Type-safe spy references using vi.mocked (vitest's official pattern).
 */
function mockPlay(): ReturnType<typeof vi.fn> & { mockClear: () => void } {
  return vi.mocked(HTMLMediaElement.prototype.play);
}
function mockPause(): ReturnType<typeof vi.fn> & { mockClear: () => void } {
  return vi.mocked(HTMLMediaElement.prototype.pause);
}
function mockCaf(): ReturnType<typeof vi.fn> & { mockClear: () => void } {
  return vi.mocked(window.cancelAnimationFrame);
}

// ── Suite ──────────────────────────────────────────────────────────
const originalCreateObjectURL = URL.createObjectURL;
const originalRevokeObjectURL = URL.revokeObjectURL;

describe("RecordingPlayer", () => {
  let blobUrlCounter = 0;

  beforeEach(() => {
    blobUrlCounter = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () => new Response(new Blob(["audio"], { type: "audio/wav" })),
      ),
    );
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => `blob:recording-${++blobUrlCounter}`),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockReturnValue(
      Promise.resolve(),
    );
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    // Return a positive handle so assertions on cancelAnimationFrame are meaningful
    vi.spyOn(window, "requestAnimationFrame").mockReturnValue(1);
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
  });

  afterEach(() => {
    cleanup();
    if (originalCreateObjectURL) {
      Object.defineProperty(URL, "createObjectURL", {
        configurable: true,
        value: originalCreateObjectURL,
      });
    } else {
      Reflect.deleteProperty(URL, "createObjectURL");
    }
    if (originalRevokeObjectURL) {
      Object.defineProperty(URL, "revokeObjectURL", {
        configurable: true,
        value: originalRevokeObjectURL,
      });
    } else {
      Reflect.deleteProperty(URL, "revokeObjectURL");
    }
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  // ── 1. Empty state ──────────────────────────────────────────────

  it("shows empty state when recordings is empty", () => {
    render(<RecordingPlayer meetingId="test-mtg" recordings={[]} />);
    expect(screen.getByText("録音ファイルはありません")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "再生" }),
    ).not.toBeInTheDocument();
  });

  // ── 2. Audio element URLs ───────────────────────────────────────

  it("fetches recordings with auth headers and renders blob src URLs for both roles", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    const audios = allAudios(container);
    expect(audios).toHaveLength(2);

    await waitForAudioSources(container);
    expect(audios.map((el) => el.getAttribute("src"))).toEqual([
      "blob:recording-1",
      "blob:recording-2",
    ]);
    expect(fetch).toHaveBeenCalledWith(
      `${TEST_BASE_URL}/meetings/test-mtg/recordings/other`,
      { headers: expect.any(Headers), signal: expect.any(AbortSignal) },
    );
    expect(fetch).toHaveBeenCalledWith(
      `${TEST_BASE_URL}/meetings/test-mtg/recordings/self`,
      { headers: expect.any(Headers), signal: expect.any(AbortSignal) },
    );
    const firstHeaders = vi.mocked(fetch).mock.calls[0][1]?.headers;
    expect(firstHeaders).toBeInstanceOf(Headers);
    expect((firstHeaders as Headers).get("Authorization")).toBe(
      "Bearer test-token",
    );
  });

  it("renders single audio element when only one role is present", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="m1" recordings={onlyOtherRecording} />,
    );
    const audios = allAudios(container);
    expect(audios).toHaveLength(1);
    await waitForAudioSources(container);
    expect(audios[0].getAttribute("src")).toBe("blob:recording-1");
    expect(fetch).toHaveBeenCalledWith(
      `${TEST_BASE_URL}/meetings/m1/recordings/other`,
      { headers: expect.any(Headers), signal: expect.any(AbortSignal) },
    );
  });

  // ── 3. Play/Pause toggle ───────────────────────────────────────

  it("calls play() on both elements and switches aria-label to pause", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);

    const playBtn = screen.getByLabelText("再生");
    fireEvent.click(playBtn);

    await waitFor(() => {
      expect(screen.getByLabelText("一時停止")).toBeInTheDocument();
    });
    expect(mockPlay()).toHaveBeenCalledTimes(2);
  });

  it("clicking pause calls pause() and returns aria-label to play", async () => {
    // First play
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);
    fireEvent.click(screen.getByLabelText("再生"));
    await waitFor(() => {
      expect(screen.getByLabelText("一時停止")).toBeInTheDocument();
    });
    mockPlay().mockClear();

    // Now pause
    const pauseBtn = screen.getByLabelText("一時停止");
    fireEvent.click(pauseBtn);

    expect(mockPause()).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText("再生")).toBeInTheDocument();
  });

  // ── 4. Play error ──────────────────────────────────────────────

  it("shows error when all play() calls reject", async () => {
    // Replace play mock with one that rejects
    vi.restoreAllMocks();
    vi.spyOn(HTMLMediaElement.prototype, "play").mockRejectedValue(
      new Error("mock failure"),
    );
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    vi.spyOn(window, "requestAnimationFrame").mockReturnValue(1);
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});

    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);
    fireEvent.click(screen.getByLabelText("再生"));

    await waitFor(() => {
      expect(
        screen.getByText(
          "録音ファイルの再生に失敗しました。ファイルが見つからないか、読み込みエラーが発生しました。",
        ),
      ).toBeInTheDocument();
    });

    // Should remain in play (not pause) state
    expect(screen.getByLabelText("再生")).toBeInTheDocument();
    expect(screen.queryByLabelText("一時停止")).not.toBeInTheDocument();
  });

  // ── 5. Seek slider ─────────────────────────────────────────────

  it("seek slider updates display time and audio element currentTime", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);
    await act(async () => {
      setupDuration(container, 120);
    });

    // Wait for duration state to propagate (triggered by loadedmetadata)
    await waitFor(() => {
      expect(screen.getByText(/\/ 2:00$/)).toBeInTheDocument();
    });

    const slider = screen.getByLabelText("シーク");
    fireEvent.change(slider, { target: { value: "45.5" } });

    // Display shows the new currentTime
    await waitFor(() => {
      expect(screen.getByText(/^0:45 \/ 2:00$/)).toBeInTheDocument();
    });

    // Audio elements had currentTime set
    const audios = allAudios(container);
    for (const audio of audios) {
      expect(audio.currentTime).toBe(45.5);
    }
  });

  // ── 6. Playback speed ──────────────────────────────────────────

  it("playback speed select updates playbackRate and audio element properties", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);

    const rateSelect = screen.getByLabelText("再生速度");
    expect(rateSelect).toHaveValue("1");

    fireEvent.change(rateSelect, { target: { value: "1.5" } });

    await waitFor(() => {
      expect(rateSelect).toHaveValue("1.5");
    });

    // Also verify the underlying audio elements' playbackRate changed
    const audios = allAudios(container);
    for (const audio of audios) {
      expect(audio.playbackRate).toBe(1.5);
    }
  });

  // ── 7. Volume / mute ───────────────────────────────────────────

  it("per-role volume sliders update volume on audio elements", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);

    const otherSlider = screen.getByLabelText("相手の音声 音量");
    fireEvent.change(otherSlider, { target: { value: "0.3" } });
    expect(otherSlider).toHaveValue("0.3");

    const selfSlider = screen.getByLabelText("自分の音声 音量");
    fireEvent.change(selfSlider, { target: { value: "0.75" } });
    expect(selfSlider).toHaveValue("0.75");

    // Verify the actual audio element volume was set
    const audios = allAudios(container);
    expect(audios[0].volume).toBe(0.3);
    expect(audios[1].volume).toBe(0.75);
  });

  it("mute buttons toggle muted state, aria-labels, and audio element muted properties", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);
    const audios = allAudios(container);

    // Both start unmuted (muted === false)
    expect(audios[0].muted).toBe(false);
    expect(audios[1].muted).toBe(false);

    // Both start as "ミュート"
    const muteBtns = screen.getAllByLabelText("ミュート");
    expect(muteBtns).toHaveLength(2);

    // Click the first mute (other)
    fireEvent.click(muteBtns[0]);

    // After first click: one becomes "ミュート解除", the other stays "ミュート"
    expect(screen.getByLabelText("ミュート解除")).toBeInTheDocument();
    expect(screen.getAllByLabelText("ミュート")).toHaveLength(1);

    // Verify the first audio element is now muted
    expect(audios[0].muted).toBe(true);
    expect(audios[1].muted).toBe(false);

    // Click the remaining "ミュート" (self)
    fireEvent.click(screen.getByLabelText("ミュート"));

    // Now both should be "ミュート解除"
    expect(screen.getAllByLabelText("ミュート解除")).toHaveLength(2);

    // Verify both audio elements are now muted
    expect(audios[0].muted).toBe(true);
    expect(audios[1].muted).toBe(true);
  });

  // ── 8. Single-track / disabled controls ─────────────────────────

  it("disables self volume controls when only other recording exists", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={onlyOtherRecording} />,
    );
    await waitForAudioSources(container);

    // Self slider is disabled
    const selfSlider = screen.getByLabelText("自分の音声 音量");
    expect(selfSlider).toBeDisabled();

    // Other slider is enabled
    const otherSlider = screen.getByLabelText("相手の音声 音量");
    expect(otherSlider).not.toBeDisabled();

    // Both mute buttons render (one disabled, one enabled) — both have
    // aria-label "ミュート" because muted state starts as false for both.
    const muteBtns = screen.getAllByLabelText("ミュート");
    expect(muteBtns).toHaveLength(2);

    // The self-section mute is disabled; the other-section mute is not
    const disabledMute = muteBtns.filter(
      (btn): btn is HTMLButtonElement =>
        btn instanceof HTMLButtonElement && btn.disabled,
    );
    const enabledMute = muteBtns.filter(
      (btn): btn is HTMLButtonElement =>
        btn instanceof HTMLButtonElement && !btn.disabled,
    );
    expect(disabledMute).toHaveLength(1);
    expect(enabledMute).toHaveLength(1);
  });

  it("disables other volume controls when only self recording exists", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={onlySelfRecording} />,
    );
    await waitForAudioSources(container);

    const otherSlider = screen.getByLabelText("相手の音声 音量");
    expect(otherSlider).toBeDisabled();

    const selfSlider = screen.getByLabelText("自分の音声 音量");
    expect(selfSlider).not.toBeDisabled();
  });

  // ── 9. Playback speed select renders all options ──────────────

  it("renders all playback speed options", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);

    const rateSelect = screen.getByLabelText("再生速度");
    const options = within(rateSelect).getAllByRole("option");
    const values = options.map((o) => o.getAttribute("value"));
    expect(values).toEqual(["0.5", "0.75", "1", "1.25", "1.5", "2"]);
  });

  // ────────────────────────────────────────────────────────────────
  // New tests for edge cases
  // ────────────────────────────────────────────────────────────────

  // ── 10. canPlayAt: no playable tracks ────────────────────────────

  it("shows error when seeking past end and clicking play (no playable tracks)", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);
    await act(async () => {
      setupDuration(container, 120);
    });
    await waitFor(() => {
      expect(screen.getByText(/\/ 2:00$/)).toBeInTheDocument();
    });

    // Seek to the very end (120 seconds = max duration)
    const slider = screen.getByLabelText("シーク");
    fireEvent.change(slider, { target: { value: "120" } });

    // Wait for currentTime state to propagate
    await waitFor(() => {
      expect(screen.getByText(/^2:00 \/ 2:00$/)).toBeInTheDocument();
    });

    // Click play — canPlayAt(el, 120) returns false for both
    // because 120 < 120 is false
    fireEvent.click(screen.getByLabelText("再生"));

    // Error should appear synchronously (via the early-return branch)
    expect(
      screen.getByText("再生可能なトラックがありません。"),
    ).toBeInTheDocument();

    // play() should NOT have been called (the early return fires before play)
    expect(mockPlay()).not.toHaveBeenCalled();
  });

  // ── 11. Partial play failure ─────────────────────────────────────

  it("enters playing state when at least one play() succeeds despite one rejection", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);

    // One resolves, one rejects
    vi.spyOn(HTMLMediaElement.prototype, "play")
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("fail"));

    fireEvent.click(screen.getByLabelText("再生"));

    // Should still enter playing state (at least one track succeeded)
    await waitFor(() => {
      expect(screen.getByLabelText("一時停止")).toBeInTheDocument();
    });

    // No error message should be shown
    expect(
      screen.queryByText("録音ファイルの再生に失敗しました。"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("再生可能なトラックがありません。"),
    ).not.toBeInTheDocument();

    // Both play() calls were attempted
    expect(mockPlay()).toHaveBeenCalledTimes(2);
  });

  // ── 12. Both tracks ended ────────────────────────────────────────

  it("resets to stopped state when both tracks reach end", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);

    // Start playing
    fireEvent.click(screen.getByLabelText("再生"));
    await waitFor(() => {
      expect(screen.getByLabelText("一時停止")).toBeInTheDocument();
    });

    // Clear prior caf calls (none expected, but be explicit)
    mockCaf().mockClear();

    // Mark both audio elements as ended and dispatch end events
    const audios = allAudios(container);
    await act(async () => {
      for (const audio of audios) {
        Object.defineProperty(audio, "ended", {
          value: true,
          configurable: true,
        });
        audio.dispatchEvent(new Event("ended"));
      }
    });

    // Should return to stopped state
    await waitFor(() => {
      expect(screen.getByLabelText("再生")).toBeInTheDocument();
    });

    // cancelAnimationFrame should have been called with the stored handle
    expect(mockCaf()).toHaveBeenCalledWith(1);
  });

  // ── 13. Unmount cleanup while playing ────────────────────────────

  it("calls cancelAnimationFrame on unmount while playing", async () => {
    const { container, unmount } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);

    // Start playing
    fireEvent.click(screen.getByLabelText("再生"));
    await waitFor(() => {
      expect(screen.getByLabelText("一時停止")).toBeInTheDocument();
    });

    // Clear prior caf calls
    mockCaf().mockClear();

    // Unmount the component — the cleanup effect should call
    // cancelAnimationFrame with the stored rAF handle (1)
    act(() => {
      unmount();
    });

    expect(mockCaf()).toHaveBeenCalledWith(1);
  });

  // ── 14. aria-pressed on mute buttons ──────────────────────────────

  it("mute buttons expose aria-pressed attribute", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);

    const muteBtns = screen.getAllByRole("button", { pressed: false });
    // Both start unpressed (muted === false, so aria-pressed="false")
    expect(muteBtns).toHaveLength(2);

    // Click first mute — it becomes pressed
    fireEvent.click(muteBtns[0]);
    const pressed = screen.getAllByRole("button", { pressed: true });
    expect(pressed).toHaveLength(1);
    const unpressed = screen.getAllByRole("button", { pressed: false });
    expect(unpressed).toHaveLength(1);
  });

  // ── 15. aria-valuetext on seek bar ─────────────────────────────

  it("seek bar has aria-valuetext with current / duration", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);
    await act(async () => {
      setupDuration(container, 120);
    });
    await waitFor(() => {
      expect(screen.getByText(/\/ 2:00$/)).toBeInTheDocument();
    });

    const slider = screen.getByLabelText("シーク");
    expect(slider).toHaveAttribute("aria-valuetext", "0:00 / 2:00");

    fireEvent.change(slider, { target: { value: "45.5" } });
    await waitFor(() => {
      expect(screen.getByText(/^0:45 \/ 2:00$/)).toBeInTheDocument();
    });
    expect(slider).toHaveAttribute("aria-valuetext", "0:45 / 2:00");
  });

  // ── 16. aria-valuetext on volume sliders ──────────────────────────

  it("volume sliders have aria-valuetext with percentage or muted text", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);

    const otherSlider = screen.getByLabelText("相手の音声 音量");
    // Default volume = 1 → 100%
    expect(otherSlider).toHaveAttribute("aria-valuetext", "100%");

    fireEvent.change(otherSlider, { target: { value: "0.3" } });
    expect(otherSlider).toHaveAttribute("aria-valuetext", "30%");

    // Mute → "ミュート中"
    const muteBtns = screen.getAllByLabelText("ミュート");
    fireEvent.click(muteBtns[0]);
    expect(otherSlider).toHaveAttribute("aria-valuetext", "ミュート中");
  });

  // ── 17. aria-valuemin / aria-valuemax / aria-valuenow on volume sliders ─

  it("volume sliders expose aria-valuemin, aria-valuemax, and aria-valuenow", async () => {
    const { container } = render(
      <RecordingPlayer meetingId="test-mtg" recordings={bothRecordings} />,
    );
    await waitForAudioSources(container);

    const otherSlider = screen.getByLabelText("相手の音声 音量");
    expect(otherSlider).toHaveAttribute("aria-valuemin", "0");
    expect(otherSlider).toHaveAttribute("aria-valuemax", "1");
    // Default volume = 1
    expect(otherSlider).toHaveAttribute("aria-valuenow", "1");

    // Change volume to 0.3
    fireEvent.change(otherSlider, { target: { value: "0.3" } });
    expect(otherSlider).toHaveAttribute("aria-valuenow", "0.3");

    // Mute → aria-valuenow becomes 0
    const muteBtns = screen.getAllByLabelText("ミュート");
    fireEvent.click(muteBtns[0]);
    expect(otherSlider).toHaveAttribute("aria-valuenow", "0");

    // Unmute → aria-valuenow returns to stored volume
    fireEvent.click(muteBtns[0]);
    expect(otherSlider).toHaveAttribute("aria-valuenow", "0.3");

    // Same for self slider
    const selfSlider = screen.getByLabelText("自分の音声 音量");
    expect(selfSlider).toHaveAttribute("aria-valuemin", "0");
    expect(selfSlider).toHaveAttribute("aria-valuemax", "1");
    expect(selfSlider).toHaveAttribute("aria-valuenow", "1");
  });
});
