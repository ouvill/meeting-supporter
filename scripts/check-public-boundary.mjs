import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

const ALLOWED_TOP_LEVEL = new Set([
  ".envrc.example",
  ".github",
  ".gitignore",
  ".python-version",
  ".vscode",
  "AGENTS.md",
  "CLA.md",
  "CODE_OF_CONDUCT.md",
  "CONTRIBUTING.md",
  "LICENSE",
  "README.md",
  "SECURITY.md",
  "THIRD-PARTY-NOTICES.txt",
  "doc",
  "flake.lock",
  "flake.nix",
  "index.html",
  "licenses",
  "openapi-ts.config.ts",
  "openapi.json",
  "package.json",
  "package-lock.json",
  "public",
  "python",
  "python-server",
  "renovate.json",
  "scripts",
  "src",
  "src-tauri",
  "test",
  "tsconfig.json",
  "tsconfig.node.json",
  "vite.config.ts",
  "vitest.config.ts",
  "wdio.live-codex.conf.ts",
  "wdio.tauri.conf.ts",
]);

const ALLOWED_DOCS = new Set([
  "doc/README.md",
  "doc/product/vision.md",
  "doc/product/prd.md",
  "doc/ui/README.md",
  "doc/ui/product-surfaces.md",
  "doc/adr/README.md",
  "doc/adr/001-python-server-directory-structure.md",
  "doc/adr/002-stt-pipeline-architecture.md",
  "doc/adr/003-meeting-recording-history-architecture.md",
  "doc/adr/009-live-reply-llm-usecase-runtime-provider-architecture.md",
  "doc/adr/010-ai-route-strategy.md",
  "doc/adr/011-general-route-card-visibility.md",
  "doc/adr/012-native-window-chrome-and-pin-preference.md",
  "doc/adr/013-contextual-api-credential-controls.md",
  "doc/adr/015-localized-ui-message-contract.md",
]);

const PATH_RULES = [
  ["wrangler-local-state", /(?:^|\/)\.wrangler(?:\/|$)/i],
  ["sqlite-file", /\.sqlite[^/]*$/i],
  ["database-file", /\.db[^/]*$/i],
  ["log-file", /\.log$/i],
  ["non-example-env-file", /(?:^|\/)\.env(?![^/]*\.example$)[^/]*$/i],
  ["private-key-file", /\.(?:pem|key)$/i],
];

const CONTENT_RULES = [
  ["private-issue-ulid", /issue:[0-9A-HJKMNP-TV-Z]{26}/i],
  ["private-issue-path", /issues\/(?:open|closed)(?:\/|\b)/i],
  ["private-repository-url", /https?:\/\/[^\s)\]}>]*meeting-supporter-private(?:[/.?#]|$)/i],
  ["linux-home-path", /\/home\/[^/\s]+/],
  ["macos-home-path", /\/Users\/[^/\s]+/],
  ["windows-home-path", /C:\\Users\\[^\\\s]+/i],
  ["managed-internal-allowlist", /MANAGED_INTERNAL_ALLOWLIST/],
  [
    "managed-internal-config-name",
    /(?:GLOBAL_(?:LLM|STT)_BUDGET_MICRO_USD|STRIPE_PRICE_ID|PRICE_VERSION|FX_VERSION|GRANT_(?:JPY|MICRO_USD)|STT_PRICE_MICRO_USD_PER_AUDIO_MINUTE|LLM_(?:INPUT|OUTPUT)_MICRO_USD_PER_MILLION_TOKENS|CLERK_SECRET_KEY|STRIPE_SECRET_KEY|STRIPE_WEBHOOK_SECRET|AI_GATEWAY_TOKEN|METADATA_HMAC_SECRET|CLOUDFLARE_API_TOKEN|CLOUDFLARE_ACCOUNT_ID)/,
  ],
];

const CONTENT_SCAN_EXCLUSIONS = new Set(["scripts/check-public-boundary.mjs"]);
const violations = new Set();

function report(path, rule) {
  violations.add(`${path}\0${rule}`);
}

const listed = spawnSync("git", ["ls-files", "-z"], {
  encoding: "buffer",
  windowsHide: true,
});

if (listed.status !== 0 || listed.error) {
  console.error("git-ls-files: command-failed");
  process.exit(1);
}

const files = listed.stdout
  .toString("utf8")
  .split("\0")
  .filter(Boolean)
  .map((path) => path.replaceAll("\\", "/"));

for (const path of files) {
  const topLevel = path.split("/", 1)[0];
  if (!ALLOWED_TOP_LEVEL.has(topLevel)) {
    report(path, "top-level-allowlist");
  }

  if (path.startsWith("doc/") && !ALLOWED_DOCS.has(path)) {
    report(path, "doc-allowlist");
  }

  for (const [rule, pattern] of PATH_RULES) {
    if (pattern.test(path)) {
      report(path, rule);
    }
  }

  if (CONTENT_SCAN_EXCLUSIONS.has(path)) {
    continue;
  }

  let buffer;
  try {
    buffer = readFileSync(path);
  } catch {
    report(path, "file-read-error");
    continue;
  }

  if (buffer.includes(0)) {
    continue;
  }

  const text = buffer.toString("utf8");
  for (const [rule, pattern] of CONTENT_RULES) {
    if (pattern.test(text)) {
      report(path, rule);
    }
  }
}

if (violations.size > 0) {
  console.error("Public boundary check failed:");
  for (const violation of [...violations].sort()) {
    const [path, rule] = violation.split("\0");
    console.error(`${path}: ${rule}`);
  }
  process.exit(1);
}

console.log(`Public boundary check passed (${files.length} tracked files).`);
