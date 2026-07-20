import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useMeetingSocket } from "./useMeetingSocket";
import { useMeetingStore } from "../store/meetingStore";

function createMockSocket() {
  return {
    readyState: 0, // CONNECTING by default
    send: vi.fn(),
    close: vi.fn(),
    onopen: null as ((event: Event) => void) | null,
    onclose: null as ((event: CloseEvent) => void) | null,
    onerror: null as ((event: Event) => void) | null,
    onmessage: null as ((event: MessageEvent) => void) | null,
  };
}

describe("useMeetingSocket", () => {
  let mockWs = createMockSocket();

  beforeEach(() => {
    useMeetingStore.setState(useMeetingStore.getInitialState());
    vi.useFakeTimers();

    vi.stubGlobal(
      "WebSocket",
      Object.assign(
        vi.fn(function WebSocketMock() {
          mockWs = createMockSocket();
          return mockWs;
        }),
        { OPEN: 1, CLOSING: 2, CLOSED: 3 },
      ),
    );
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("connects when apiPort is provided", () => {
    renderHook(() => useMeetingSocket(8000, "token"));
    expect(WebSocket).toHaveBeenCalledWith("ws://127.0.0.1:8000/ws", [
      "auth.token",
    ]);
  });

  it("does not connect when apiPort is null", () => {
    renderHook(() => useMeetingSocket(null, "token"));
    expect(WebSocket).not.toHaveBeenCalled();
  });

  it("does not connect when apiAuthToken is null", () => {
    renderHook(() => useMeetingSocket(8000, null));
    expect(WebSocket).not.toHaveBeenCalled();
  });

  it("sets connected on open", () => {
    renderHook(() => useMeetingSocket(8000, "token"));

    act(() => {
      mockWs.onopen?.(new Event("open"));
    });

    expect(useMeetingStore.getState().connected).toBe(true);
  });

  it("reconnects after close", async () => {
    renderHook(() => useMeetingSocket(8000, "token"));
    const initialSocket = mockWs;

    act(() => {
      initialSocket.onopen?.(new Event("open"));
      initialSocket.onclose?.(new CloseEvent("close"));
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1999);
    });
    expect(WebSocket).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(WebSocket).toHaveBeenCalledTimes(2);
    expect(mockWs).not.toBe(initialSocket);
  });

  it("dispatches valid inbound message", () => {
    renderHook(() => useMeetingSocket(8000, "token"));

    act(() => {
      mockWs.onopen?.(new Event("open"));
      mockWs.onmessage?.(
        new MessageEvent("message", {
          data: JSON.stringify({ type: "status", text: "OK" }),
        }),
      );
    });

    expect(useMeetingStore.getState().statusText).toBe("OK");
  });

  it("ignores malformed JSON", () => {
    renderHook(() => useMeetingSocket(8000, "token"));

    act(() => {
      mockWs.onopen?.(new Event("open"));
      mockWs.onmessage?.(new MessageEvent("message", { data: "not-json" }));
    });

    expect(useMeetingStore.getState().statusText).toBe("接続中...");
  });

  it("ignores invalid message shape", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    renderHook(() => useMeetingSocket(8000, "token"));

    act(() => {
      mockWs.onopen?.(new Event("open"));
      mockWs.onmessage?.(
        new MessageEvent("message", {
          data: JSON.stringify({ type: "unknown_type" }),
        }),
      );
    });

    expect(useMeetingStore.getState().statusText).toBe("接続中...");
  });

  it("send posts JSON when open", () => {
    const { result } = renderHook(() => useMeetingSocket(8000, "token"));
    mockWs.readyState = WebSocket.OPEN;

    act(() => {
      mockWs.onopen?.(new Event("open"));
    });

    result.current.send({ type: "start_meeting" });
    expect(mockWs.send).toHaveBeenCalledWith(
      JSON.stringify({ type: "start_meeting" }),
    );
  });

  it("send is no-op when not open", () => {
    const { result } = renderHook(() => useMeetingSocket(8000, "token"));
    // do not call onopen
    result.current.send({ type: "start_meeting" });
    expect(mockWs.send).not.toHaveBeenCalled();
  });

  it("cleans up on unmount", () => {
    const { unmount } = renderHook(() => useMeetingSocket(8000, "token"));

    act(() => {
      mockWs.onopen?.(new Event("open"));
    });
    unmount();

    expect(mockWs.close).toHaveBeenCalled();
    expect(useMeetingStore.getState().connected).toBe(false);
  });

  // ── sttInitRequested ──────────────────────────────────────────────

  it("send init_stt sets sttInitRequested when socket open", () => {
    const { result } = renderHook(() => useMeetingSocket(8000, "token"));
    mockWs.readyState = WebSocket.OPEN;

    act(() => {
      mockWs.onopen?.(new Event("open"));
    });

    result.current.send({ type: "init_stt" });
    expect(mockWs.send).toHaveBeenCalledWith(
      JSON.stringify({ type: "init_stt" }),
    );
    expect(useMeetingStore.getState().sttInitRequested).toBe(true);
  });

  it("send shutdown_stt clears sttInitRequested when socket open", () => {
    const { result } = renderHook(() => useMeetingSocket(8000, "token"));
    mockWs.readyState = WebSocket.OPEN;

    act(() => {
      mockWs.onopen?.(new Event("open"));
    });

    result.current.send({ type: "init_stt" });
    expect(useMeetingStore.getState().sttInitRequested).toBe(true);

    result.current.send({ type: "shutdown_stt" });
    expect(mockWs.send).toHaveBeenLastCalledWith(
      JSON.stringify({ type: "shutdown_stt" }),
    );
    expect(useMeetingStore.getState().sttInitRequested).toBe(false);
  });

  it("send init_stt does not set sttInitRequested when socket not open", () => {
    const { result } = renderHook(() => useMeetingSocket(8000, "token"));
    // Do not call onopen — socket remains CONNECTING
    result.current.send({ type: "init_stt" });
    expect(mockWs.send).not.toHaveBeenCalled();
    expect(useMeetingStore.getState().sttInitRequested).toBe(false);
  });

  it("onclose clears sttInitRequested after init_stt was sent", () => {
    const { result } = renderHook(() => useMeetingSocket(8000, "token"));
    mockWs.readyState = WebSocket.OPEN;

    act(() => {
      mockWs.onopen?.(new Event("open"));
    });
    expect(useMeetingStore.getState().connected).toBe(true);

    result.current.send({ type: "init_stt" });
    expect(useMeetingStore.getState().sttInitRequested).toBe(true);

    act(() => {
      mockWs.onclose?.(new CloseEvent("close"));
    });
    expect(useMeetingStore.getState().sttInitRequested).toBe(false);
  });
});
