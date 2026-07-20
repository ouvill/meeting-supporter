import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { useMeetingStore } from "../store/meetingStore";

const setAssistantWindowVisibleMock = vi.hoisted(() =>
  vi.fn<(visible: boolean) => Promise<void>>(async () => {}),
);

vi.mock("../hooks/useBackendBootstrapStatus", () => ({
  useBackendBootstrapStatus: () => ({
    apiPort: 8000,
    apiAuthToken: "token",
    bootstrap: { phase: "running", message: "Backend is ready." },
    crashInfo: null,
  }),
}));

vi.mock("../hooks/useMeetingSocket", () => ({
  useMeetingSocket: () => ({ send: vi.fn() }),
}));

vi.mock("../platform/tauriWindow", () => ({
  getCurrentAppWindowLabel: () => "main",
  setAssistantWindowVisible: setAssistantWindowVisibleMock,
  setWindowAlwaysOnTop: vi.fn(async () => {}),
  readWindowAlwaysOnTop: vi.fn(async () => false),
  onCurrentWindowFocused: vi.fn(async () => () => undefined),
}));

describe("App window and reconnection behavior", () => {
  beforeEach(() => {
    useMeetingStore.getState().reset();
    window.localStorage.clear();
    setAssistantWindowVisibleMock.mockClear();
  });

  it("keeps the prompter closed until requested and hides it when the meeting ends", async () => {
    render(<App />);
    setAssistantWindowVisibleMock.mockClear();

    act(() => {
      useMeetingStore.setState({ connected: true, isRunning: true });
    });
    await screen.findByRole("main", { name: "会話ワークスペース" });
    expect(setAssistantWindowVisibleMock).not.toHaveBeenCalledWith(true);

    act(() => {
      useMeetingStore.setState({ isRunning: false });
    });
    await waitFor(() =>
      expect(setAssistantWindowVisibleMock).toHaveBeenCalledWith(false),
    );
  });

  it("renders the meeting-control landmark and exposes recovery of the assistant window", async () => {
    render(<App />);

    act(() => {
      useMeetingStore.setState({ connected: true, isRunning: true });
    });

    const controls = await screen.findByRole("main", {
      name: "会話ワークスペース",
    });
    expect(controls).toBeInTheDocument();
    expect(
      screen.getByRole("navigation", { name: "アプリツールバー" }),
    ).toBeInTheDocument();

    setAssistantWindowVisibleMock.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "プロンプターに表示" }));
    expect(setAssistantWindowVisibleMock).toHaveBeenCalledWith(true);
  });

  it("keeps the setup screen mounted while reconnecting", async () => {
    render(<App />);

    await screen.findByRole("navigation", { name: "アプリツールバー" });
    expect(
      screen.getByRole("heading", { name: "まず、音声を確認しましょう" }),
    ).toBeInTheDocument();
    act(() => {
      useMeetingStore.setState({ connected: false });
    });

    expect(
      screen.getByText(
        "画面はそのままにしてお待ちください。操作は接続後に再開できます。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "まず、音声を確認しましょう" }),
    ).toBeInTheDocument();
  });

  it("continues normally when first-run persistence is unavailable", async () => {
    const setItem = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new Error("storage denied");
      });

    render(<App />);
    act(() => {
      useMeetingStore.setState({ connected: true, isRunning: true });
    });
    await screen.findByRole("main", { name: "会話ワークスペース" });

    act(() => {
      useMeetingStore.setState({ isRunning: false });
    });
    expect(
      await screen.findByRole("heading", {
        name: "次の会議を準備しましょう",
      }),
    ).toBeInTheDocument();
    expect(
      window.localStorage.getItem("meeting-supporter.first-meeting-started"),
    ).toBeNull();

    setItem.mockRestore();
  });
  it("retires first-run guidance only after a meeting starts", async () => {
    const firstRender = render(<App />);

    expect(
      await screen.findByRole("heading", {
        name: "まず、音声を確認しましょう",
      }),
    ).toBeInTheDocument();

    act(() => {
      useMeetingStore.setState({ connected: true, isRunning: true });
    });
    await screen.findByRole("main", { name: "会話ワークスペース" });
    await waitFor(() =>
      expect(
        window.localStorage.getItem("meeting-supporter.first-meeting-started"),
      ).toBe("true"),
    );

    act(() => {
      useMeetingStore.setState({ isRunning: false });
    });
    expect(
      await screen.findByRole("heading", {
        name: "次の会議を準備しましょう",
      }),
    ).toBeInTheDocument();

    firstRender.unmount();
    render(<App />);
    expect(
      await screen.findByRole("heading", {
        name: "次の会議を準備しましょう",
      }),
    ).toBeInTheDocument();
  });
});
