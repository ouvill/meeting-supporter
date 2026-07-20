if (import.meta.env.MODE === "wdio") {
  void import("@wdio/tauri-plugin");
}

export {};
