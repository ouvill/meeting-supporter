import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  ApiConnectionControl,
  type ApiConnectionControlProps,
} from "./ApiConnectionControl";

function props(
  overrides: Partial<ApiConnectionControlProps> = {},
): ApiConnectionControlProps {
  return {
    provider: "gemini",
    state: "unconfigured",
    hasSavedKey: false,
    draftKey: "",
    editing: false,
    testing: false,
    testMessage: null,
    onBeginEdit: vi.fn(),
    onCancelEdit: vi.fn(),
    onDraftChange: vi.fn(),
    onTest: vi.fn(),
    onRequestDelete: vi.fn(),
    onCancelDelete: vi.fn(),
    ...overrides,
  };
}

describe("ApiConnectionControl", () => {
  it("accepts an unconfigured password draft and keeps testing disabled while blank", () => {
    const controlProps = props();
    render(<ApiConnectionControl {...controlProps} />);

    const input = screen.getByLabelText("Google Gemini APIキー");
    expect(input).toHaveAttribute("type", "password");
    expect(
      screen.getByRole("button", { name: "Google Gemini 接続を確認" }),
    ).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "APIキーを表示" }));
    expect(input).toHaveAttribute("type", "text");
    fireEvent.change(input, { target: { value: "gemini-draft" } });
    expect(controlProps.onDraftChange).toHaveBeenCalledWith("gemini-draft");
  });

  it("never displays a saved value and delegates the edit lifecycle", () => {
    const onBeginEdit = vi.fn();
    const onCancelEdit = vi.fn();
    const compactProps = props({
      state: "saved-unverified",
      hasSavedKey: true,
      onBeginEdit,
      onCancelEdit,
    });
    const { rerender } = render(
      <ApiConnectionControl {...compactProps} />,
    );

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue(/saved/i)).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Google Gemini APIキーを変更" }),
    );
    expect(onBeginEdit).toHaveBeenCalledOnce();

    rerender(
      <ApiConnectionControl {...compactProps} editing draftKey="" />,
    );
    expect(screen.getByLabelText("Google Gemini APIキー")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "変更をキャンセル" }));
    expect(onCancelEdit).toHaveBeenCalledOnce();
  });

  it("confirms a saved-key deletion before delegating it", () => {
    const onRequestDelete = vi.fn();
    render(
      <ApiConnectionControl
        {...props({
          state: "saved-unverified",
          hasSavedKey: true,
          onRequestDelete,
        })}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Google Gemini APIキーを削除" }),
    );
    expect(
      screen.getByText("保存済みのAPIキーを削除しますか？"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "キャンセル" }));
    expect(onRequestDelete).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole("button", { name: "Google Gemini APIキーを削除" }),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Google Gemini APIキーを削除予定にする",
      }),
    );
    expect(onRequestDelete).toHaveBeenCalledOnce();
  });

  it("replaces the input with a pending-delete recovery action", () => {
    const onCancelDelete = vi.fn();
    render(
      <ApiConnectionControl
        {...props({
          state: "pending-delete",
          hasSavedKey: true,
          onCancelDelete,
        })}
      />,
    );

    expect(screen.queryByLabelText("Google Gemini APIキー")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Google Gemini APIキーの削除を取り消す",
      }),
    );
    expect(onCancelDelete).toHaveBeenCalledOnce();
  });

  it("resets plaintext visibility after a parent-driven collapse", () => {
    const editingProps = props({
      state: "draft-unverified",
      hasSavedKey: true,
      draftKey: "replacement",
      editing: true,
    });
    const { rerender } = render(
      <ApiConnectionControl {...editingProps} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "APIキーを表示" }));
    expect(screen.getByLabelText("Google Gemini APIキー")).toHaveAttribute(
      "type",
      "text",
    );

    rerender(
      <ApiConnectionControl
        {...editingProps}
        state="saved-unverified"
        draftKey=""
        editing={false}
      />,
    );
    rerender(
      <ApiConnectionControl
        {...editingProps}
        state="saved-unverified"
        draftKey=""
      />,
    );
    expect(screen.getByLabelText("Google Gemini APIキー")).toHaveAttribute(
      "type",
      "password",
    );
  });

  it("shows a failed verification message inside the control", () => {
    render(
      <ApiConnectionControl
        {...props({
          state: "failed",
          draftKey: "invalid-key",
          testMessage: "接続を確認できませんでした。",
        })}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "接続を確認できませんでした。",
    );
  });
});
