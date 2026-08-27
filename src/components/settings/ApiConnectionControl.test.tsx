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

  it("disables every credential action when the control is locked", () => {
    const controlProps = props({
      state: "saved-unverified",
      hasSavedKey: true,
      disabled: true,
    });
    const { rerender } = render(
      <ApiConnectionControl {...controlProps} />,
    );

    expect(
      screen.getByRole("button", { name: "Google Gemini APIキーを変更" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Google Gemini 接続を確認" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Google Gemini APIキーを削除" }),
    ).toBeDisabled();

    rerender(
      <ApiConnectionControl
        {...controlProps}
        state="draft-unverified"
        editing
        draftKey="replacement"
      />,
    );
    expect(screen.getByLabelText("Google Gemini APIキー")).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "APIキーを表示" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "変更をキャンセル" }),
    ).toBeDisabled();

    rerender(
      <ApiConnectionControl
        {...controlProps}
        state="pending-delete"
      />,
    );
    expect(
      screen.getByRole("button", {
        name: "Google Gemini APIキーの削除を取り消す",
      }),
    ).toBeDisabled();
  });

  it("does not lock an unrelated provider", () => {
    const locked = props({
      provider: "openai",
      state: "saved-unverified",
      hasSavedKey: true,
      disabled: true,
    });
    const editable = props({
      provider: "gemini",
      state: "saved-unverified",
      hasSavedKey: true,
    });
    render(
      <>
        <ApiConnectionControl {...locked} />
        <ApiConnectionControl {...editable} />
      </>,
    );

    expect(
      screen.getByRole("button", { name: "OpenAI APIキーを変更" }),
    ).toBeDisabled();
    const editableButton = screen.getByRole("button", {
      name: "Google Gemini APIキーを変更",
    });
    expect(editableButton).toBeEnabled();
    fireEvent.click(editableButton);
    expect(editable.onBeginEdit).toHaveBeenCalledOnce();
    expect(locked.onBeginEdit).not.toHaveBeenCalled();
  });

  it("locks an already-open delete confirmation", () => {
    const controlProps = props({
      state: "saved-unverified",
      hasSavedKey: true,
    });
    const { rerender } = render(
      <ApiConnectionControl {...controlProps} />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Google Gemini APIキーを削除" }),
    );
    rerender(<ApiConnectionControl {...controlProps} disabled />);

    expect(
      screen.getByRole("button", {
        name: "Google Gemini APIキーを削除予定にする",
      }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "キャンセル" })).toBeDisabled();
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
