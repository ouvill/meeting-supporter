import { useState } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OllamaModelSettings } from "./OllamaModelSettings";

const { getModels } = vi.hoisted(() => ({ getModels: vi.fn() }));
vi.mock("../../api/generated/sdk.gen", () => ({
  getOllamaModelsApiSettingsOllamaModelsGet: getModels,
}));

function result(models: string[]) {
  return {
    data: {
      ok: true,
      base_url: "http://localhost:11434/v1",
      models,
      message: null,
    },
    error: undefined,
  };
}

function Settings() {
  const [baseUrl, setBaseUrl] = useState("http://localhost:11434/v1");
  const [model, setModel] = useState("synthetic-saved:8b");
  return (
    <OllamaModelSettings
      baseUrl={baseUrl}
      model={model}
      onBaseUrlChange={setBaseUrl}
      onModelChange={setModel}
    />
  );
}

describe("Ollama model selection", () => {
  beforeEach(() => vi.resetAllMocks());

  it("ignores a late response after changing URLs and preserves the selected model", async () => {
    let resolveFirst!: (value: ReturnType<typeof result>) => void;
    getModels
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
      )
      .mockResolvedValueOnce(result(["synthetic-new:8b"]));
    render(<Settings />);
    const model = screen.getByRole("combobox", { name: "Ollamaモデル" });
    expect(model).toBeDisabled();
    await waitFor(() => expect(getModels).toHaveBeenCalledOnce());

    const url = screen.getByRole("textbox", { name: "OllamaベースURL" });
    fireEvent.change(url, { target: { value: "http://localhost:1143" } });
    fireEvent.change(url, { target: { value: "http://localhost:11435/v1" } });
    await screen.findByRole("option", { name: "synthetic-new:8b" });
    expect(getModels).toHaveBeenCalledTimes(2);
    expect(getModels).toHaveBeenLastCalledWith(
      expect.objectContaining({
        query: { base_url: "http://localhost:11435/v1" },
      }),
    );
    expect(model).toHaveValue("synthetic-saved:8b");
    expect(
      screen.getByText(/選択中のモデルが接続先に見つかりません/),
    ).toBeVisible();

    await act(async () => resolveFirst(result(["synthetic-old:8b"])));
    expect(
      screen.queryByRole("option", { name: "synthetic-old:8b" }),
    ).not.toBeInTheDocument();
    fireEvent.change(model, { target: { value: "synthetic-new:8b" } });
    expect(model).toHaveValue("synthetic-new:8b");
    expect(
      screen.queryByText(/選択中のモデルが接続先に見つかりません/),
    ).not.toBeInTheDocument();

    fireEvent.change(url, { target: { value: "" } });
    expect(model).toBeDisabled();
    expect(model).toHaveValue("synthetic-new:8b");
    expect(screen.getByText("ベースURLを入力してください。")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "モデル一覧を更新" }),
    ).toBeDisabled();
  });

  it.each(["connection", "request"])(
    "recovers from a %s failure through empty and populated catalogs",
    async (failure) => {
      if (failure === "connection") {
        getModels.mockResolvedValueOnce({
          data: { ...result([]).data, ok: false },
        });
      } else {
        getModels.mockRejectedValueOnce(new Error("synthetic request failure"));
      }
      getModels
        .mockResolvedValueOnce(result([]))
        .mockResolvedValueOnce(
          result(["synthetic-saved:8b", "synthetic-other:4b"]),
        );
      render(<Settings />);
      const model = screen.getByRole("combobox", { name: "Ollamaモデル" });
      expect(await screen.findByRole("alert")).toHaveTextContent(
        /モデル一覧を取得できませんでした/,
      );
      expect(model).toBeDisabled();
      expect(model).toHaveValue("synthetic-saved:8b");

      fireEvent.click(screen.getByRole("button", { name: "モデル一覧を更新" }));
      expect(
        await screen.findByText(/接続できましたが、モデルがありません/),
      ).toBeVisible();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(model).toBeDisabled();
      expect(model).toHaveValue("synthetic-saved:8b");

      fireEvent.click(screen.getByRole("button", { name: "モデル一覧を更新" }));
      await screen.findByRole("option", { name: "synthetic-other:4b" });
      expect(model).toBeEnabled();
      expect(model).toHaveValue("synthetic-saved:8b");
      expect(
        screen.getByRole("textbox", { name: "OllamaベースURL" }),
      ).toBeVisible();
    },
  );
});
