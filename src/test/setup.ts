import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeEach, vi } from "vitest";

const originalConsoleError = console.error;
const unexpectedConsoleErrors: unknown[][] = [];

console.error = (...args: unknown[]): void => {
  unexpectedConsoleErrors.push(args);
  Reflect.apply(originalConsoleError, console, args);
};

beforeEach(() => {
  unexpectedConsoleErrors.length = 0;
});

afterEach(() => {
  if (unexpectedConsoleErrors.length === 0) return;
  const formattedCalls = unexpectedConsoleErrors
    .map((args) => args.map(String).join(" "))
    .join("\n");
  throw new Error(`Unexpected console.error calls:\n${formattedCalls}`);
});

afterAll(() => {
  console.error = originalConsoleError;
});
class MockResizeObserver implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

vi.stubGlobal("ResizeObserver", MockResizeObserver);
export const tauriWindowMockState = {
  alwaysOnTop: false,
};

// ---------------------------------------------------------------------------
// Shared Tauri API mocks
//
// Any module that imports from @tauri-apps/*  will see these defaults.
// Test files that need fine-grained control can override with their own
// vi.mock() call — test-level mocks take precedence over setup-level ones.
// ---------------------------------------------------------------------------

vi.mock("@tauri-apps/api/core", () => ({
  isTauri: () => false,
  invoke: vi.fn((cmd: string) => {
    switch (cmd) {
      case "get_backend_bootstrap_snapshot":
        return Promise.resolve({
          phase: "initializing",
          message: "Pythonバックエンドを起動しています...",
          running: false,
          port: null,
          auth_token: null,
          crash: null,
        });
      default:
        return Promise.reject(new Error(`Unknown command: ${cmd}`));
    }
  }),
}));

vi.mock("@tauri-apps/api/window", () => ({
  Window: class MockWindow {
    label: string;

    constructor(label: string) {
      this.label = label;
    }

    static async getByLabel(label: string): Promise<MockWindow | null> {
      return new MockWindow(label);
    }

    async show(): Promise<void> {}
    async hide(): Promise<void> {}
  },
  getCurrentWindow: () => ({
    label: "main",
    setAlwaysOnTop: vi.fn(async (alwaysOnTop: boolean): Promise<void> => {
      tauriWindowMockState.alwaysOnTop = alwaysOnTop;
    }),
    isAlwaysOnTop: vi.fn(
      async (): Promise<boolean> => tauriWindowMockState.alwaysOnTop,
    ),
    hide: vi.fn(async (): Promise<void> => {}),
  }),
}));

vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn(async (_options?: object): Promise<string | null> => null),
}));
