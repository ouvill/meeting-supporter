import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BootstrapScreen } from "./BootstrapScreen";

describe("BootstrapScreen", () => {
  it("keeps startup in the main landmark and tells the user preparation is in progress", () => {
    render(
      <BootstrapScreen phase="starting" message="internal bootstrap detail" />,
    );

    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByText("準備しています…")).toBeInTheDocument();
    expect(
      screen.getByText(
        "会議の準備を整えています。このまま少しお待ちください。",
      ),
    ).toBeInTheDocument();
  });

  it("maps an unexpected backend stop to a safe restart path without exposing process diagnostics", () => {
    render(
      <BootstrapScreen
        phase="failed"
        message="process exited with code 137"
        crashInfo={{
          unexpected: true,
          exit_code: 137,
          signal: null,
          message: "process exited with code 137",
        }}
      />,
    );

    expect(screen.getByText("会議サポートが停止しました")).toBeInTheDocument();
    expect(
      screen.getByText(
        "安全のため処理を停止しました。アプリを終了して、もう一度開いてください。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("process exited with code 137"),
    ).not.toBeInTheDocument();
  });
});
