import { createContext, useContext } from "react";

export const RuntimeContext = createContext<"python" | "rust-backend">(
  "python",
);
export const useRustBackend = () =>
  useContext(RuntimeContext) === "rust-backend";
