import "./platform/wdioPlugin";
import React from "react";
import ReactDOM from "react-dom/client";
import "./index.css";
import App from "./App";
import { getCurrentAppWindowLabel } from "./platform/tauriWindow";

document.title =
  getCurrentAppWindowLabel() === "assistant" ? "ライブ返答支援" : "会議支援AI";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
