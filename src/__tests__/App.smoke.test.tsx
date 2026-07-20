import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "../App";

vi.mock("../hooks/useBackendBootstrapStatus", () => ({
  useBackendBootstrapStatus: () => ({
    apiPort: null,
    apiAuthToken: null,
    bootstrap: { phase: "starting", message: "backend boot in progress" },
    crashInfo: null,
  }),
}));

vi.mock("../hooks/useMeetingSocket", () => ({
  useMeetingSocket: () => ({ send: vi.fn() }),
}));

describe("App bootstrap boundary", () => {
  it("keeps the application shell available while the backend is unavailable", () => {
    render(<App />);

    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByText("準備しています…")).toBeInTheDocument();
    expect(
      screen.getByText(
        "会議の準備を整えています。このまま少しお待ちください。",
      ),
    ).toBeInTheDocument();
  });
});
