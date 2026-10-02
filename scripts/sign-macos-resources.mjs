import { execFile } from "node:child_process";
import { randomBytes } from "node:crypto";
import {
  lstat,
  mkdtemp,
  open,
  readdir,
  realpath,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { root, run } from "./native-runtime.mjs";

async function signingKeychain(action) {
  const certificate = process.env.APPLE_CERTIFICATE;
  if (!certificate)
    return action(process.env.APPLE_SIGNING_IDENTITY || "-", []);
  // beforeBuildCommand precedes Tauri's own certificate import. Use a separate
  // temporary keychain without changing the user's default/search keychains.
  const temporary = await mkdtemp(join(tmpdir(), "meeting-sign-"));
  const keychain = join(temporary, "resources.keychain-db");
  const password = randomBytes(24).toString("hex");
  const security = (args) => run("security", args, { stdio: "ignore" });
  try {
    const file = join(temporary, "certificate.p12");
    await writeFile(file, Buffer.from(certificate, "base64"), { mode: 0o600 });
    await security(["create-keychain", "-p", password, keychain]);
    await security(["unlock-keychain", "-p", password, keychain]);
    await security(["set-keychain-settings", "-t", "3600", "-u", keychain]);
    await security([
      "import",
      file,
      "-k",
      keychain,
      "-P",
      process.env.APPLE_CERTIFICATE_PASSWORD || "",
      "-T",
      "/usr/bin/codesign",
    ]);
    await security([
      "set-key-partition-list",
      "-S",
      "apple-tool:,apple:,codesign:",
      "-s",
      "-k",
      password,
      keychain,
    ]);
    let identities;
    try {
      ({ stdout: identities } = await promisify(execFile)("security", [
        "find-identity",
        "-v",
        "-p",
        "codesigning",
        keychain,
      ]));
    } catch {
      throw new Error("Unable to inspect the resource signing certificate.");
    }
    const matches = [...identities.matchAll(/\b([A-Fa-f0-9]{40}) "([^"]+)"/g)];
    const requested = process.env.APPLE_SIGNING_IDENTITY;
    const identity = matches.find(
      (match) =>
        !requested || match[1] === requested || match[2].includes(requested),
    );
    if (!identity)
      throw new Error(
        "Resource signing identity does not match the certificate.",
      );
    return await action(identity[1], ["--keychain", keychain]);
  } finally {
    await security(["delete-keychain", keychain]).catch(() => {});
    await rm(temporary, { recursive: true, force: true });
  }
}

// Tauri signs its executable and externalBin entries, but does not recursively
// sign code placed in Resources. Sign our helper executables and libraries
// inside-out before Tauri seals the outer app. Local/CI builds use ad-hoc signing.
export async function signMacResources(directories) {
  if (process.platform !== "darwin") return;
  return signingKeychain(async (identity, keychain) => {
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
          await run("codesign", [
            "--force",
            "--sign",
            identity,
            ...keychain,
            ...(identity === "-" ? [] : ["--timestamp"]),
            resolved,
          ]);
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
          0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe, 0xcafebabe,
          0xbebafeca, 0xcafebabf, 0xbfbafeca,
        ].includes(header.readUInt32BE())
      )
        return;
      await run("codesign", [
        "--force",
        "--sign",
        identity,
        ...keychain,
        "--entitlements",
        join(root, "src-tauri/Entitlements.plist"),
        ...(identity === "-" ? [] : ["--options", "runtime", "--timestamp"]),
        resolved,
      ]);
    }
    for (const directory of directories) await visit(directory);
  });
}
