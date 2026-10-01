import { lstat, open, readdir, realpath } from "node:fs/promises";
import { join } from "node:path";
import { root, run } from "./native-runtime.mjs";

// Tauri signs its executable and externalBin entries, but does not recursively
// sign code placed in Resources. Sign our helper executables and libraries
// inside-out before Tauri seals the outer app. Local/CI builds use ad-hoc signing.
export async function signMacResources(directories) {
  if (process.platform !== "darwin") return;
  const identity = process.env.APPLE_SIGNING_IDENTITY || "-";
  const visited = new Set();
  async function visit(path) {
    const resolved = await realpath(path);
    if (visited.has(resolved)) return;
    visited.add(resolved);
    const info = await lstat(resolved);
    if (info.isDirectory()) {
      for (const name of await readdir(resolved))
        await visit(join(resolved, name));
      if (resolved.endsWith(".framework"))
        await run("codesign", ["--force", "--sign", identity, resolved]);
      return;
    }
    if (!info.isFile()) return;
    const file = await open(resolved, "r");
    const header = Buffer.alloc(4);
    try {
      await file.read(header, 0, 4, 0);
    } finally {
      await file.close();
    }
    // Mach-O (both endian forms) and universal/fat binaries.
    if (
      ![
        0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe, 0xcafebabe, 0xbebafeca,
        0xcafebabf, 0xbfbafeca,
      ].includes(header.readUInt32BE())
    )
      return;
    await run("codesign", [
      "--force",
      "--sign",
      identity,
      "--entitlements",
      join(root, "src-tauri/Entitlements.plist"),
      ...(identity === "-" ? [] : ["--options", "runtime", "--timestamp"]),
      resolved,
    ]);
  }
  for (const directory of directories) await visit(directory);
}
