import { lstatSync, readFileSync, readlinkSync } from "node:fs";
import { posix, win32 } from "node:path";
import { spawnSync } from "node:child_process";

const ALLOWED_TOP_LEVEL = new Set([
  ".envrc.example",
  ".omp",
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
  "website",
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
const PATH_SEMANTICS = [posix, win32];

function isAbsoluteLink(linkText) {
  return PATH_SEMANTICS.some((pathApi) => pathApi.isAbsolute(linkText));
}

function isOutsideRepository(path, linkText) {
  return PATH_SEMANTICS.some((pathApi) => {
    const target = pathApi.normalize(
      pathApi.join(pathApi.dirname(path), linkText),
    );
    return target === ".." || target.startsWith(`..${pathApi.sep}`);
  });
}

function report(path, rule) {
  violations.add(`${path}\0${rule}`);
}

function gitLsFileRecords(args) {
  const listed = spawnSync("git", ["ls-files", "-z", ...args], {
    encoding: "buffer",
    windowsHide: true,
  });

  if (listed.status !== 0 || listed.error) {
    console.error("git-ls-files: command-failed");
    process.exit(1);
  }

  const output = listed.stdout.toString("utf8");
  if (output !== "" && !output.endsWith("\0")) {
    console.error("git-ls-files: output-parse-failed");
    process.exit(1);
  }

  return output.split("\0").filter(Boolean);
}

function gitLsFiles(args) {
  return gitLsFileRecords(args).map((path) => path.replaceAll("\\", "/"));
}

function gitIndexEntries() {
  const entries = [];

  for (const record of gitLsFileRecords(["--stage"])) {
    const match = /^([0-7]{6}) [0-9a-f]+ ([0-3])\t(.+)$/s.exec(record);
    if (match === null) {
      console.error("git-ls-files: output-parse-failed");
      process.exit(1);
    }

    entries.push({
      mode: match[1],
      stage: Number(match[2]),
      path: match[3].replaceAll("\\", "/"),
    });
  }

  return entries;
}

const indexEntries = gitIndexEntries();
const trackedSymlinks = new Set(
  indexEntries
    .filter(({ mode, stage }) => mode === "120000" && stage === 0)
    .map(({ path }) => path),
);
for (const { path, stage } of indexEntries) {
  if (stage !== 0) {
    report(path, "unmerged-index-entry");
  }
}

const deleted = new Set(gitLsFiles(["--deleted"]));
const files = [
  ...new Set(
    gitLsFiles(["--cached", "--others", "--exclude-standard"]).filter(
      (path) => !deleted.has(path),
    ),
  ),
].sort();

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

  let stats;
  try {
    stats = lstatSync(path);
  } catch {
    report(path, "file-read-error");
    continue;
  }

  let text;
  if (stats.isSymbolicLink() || trackedSymlinks.has(path)) {
    try {
      text = stats.isSymbolicLink()
        ? readlinkSync(path, "utf8")
        : readFileSync(path, "utf8");
    } catch {
      report(path, "file-read-error");
      continue;
    }

    if (isAbsoluteLink(text)) {
      report(path, "absolute-symlink-target");
    } else if (isOutsideRepository(path, text)) {
      report(path, "external-symlink-target");
    }
  } else if (stats.isFile()) {
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
    text = buffer.toString("utf8");
  } else {
    continue;
  }

  if (CONTENT_SCAN_EXCLUSIONS.has(path)) {
    continue;
  }

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

console.log(`Public boundary check passed (${files.length} working-tree files).`);
