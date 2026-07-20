import { existsSync, readFileSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const REQUIRED_AUTHORITIES = [
  "README.md",
  "SECURITY.md",
  "CONTRIBUTING.md",
  "CODE_OF_CONDUCT.md",
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
];

const REQUIRED_LINKS = [
  {
    file: "doc/README.md",
    value: "https://github.com/ouvill/meeting-supporter/issues",
    rule: "github-issues-link",
  },
  {
    file: "SECURITY.md",
    value: "https://github.com/ouvill/meeting-supporter/security/advisories/new",
    rule: "private-vulnerability-reporting-link",
  },
  {
    file: ".github/ISSUE_TEMPLATE/config.yml",
    value: "https://github.com/ouvill/meeting-supporter/security/advisories/new",
    rule: "issue-template-security-link",
  },
];

const errors = [];

function relativeFromRoot(path) {
  return relative(ROOT, path).replaceAll("\\", "/");
}

for (const path of REQUIRED_AUTHORITIES) {
  if (!existsSync(resolve(ROOT, path))) {
    errors.push(`${path}: required-authority-missing`);
  }
}

const listed = spawnSync("git", ["ls-files", "-z", "--", "*.md"], {
  cwd: ROOT,
  encoding: "buffer",
  windowsHide: true,
});

if (listed.status !== 0 || listed.error) {
  console.error("Documentation check failed:");
  console.error("git-ls-files: command-failed");
  process.exit(1);
}

const markdownFiles = listed.stdout
  .toString("utf8")
  .split("\0")
  .filter(Boolean)
  .map((path) => path.replaceAll("\\", "/"));

const markdownLink = /!?\[[^\]]*\]\(([^)]+)\)/g;

for (const source of markdownFiles) {
  const absoluteSource = resolve(ROOT, source);
  const text = readFileSync(absoluteSource, "utf8");

  for (const match of text.matchAll(markdownLink)) {
    let target = match[1].trim();
    if (target.startsWith("<") && target.endsWith(">")) {
      target = target.slice(1, -1);
    }
    target = target.split(/\s+["']/u, 1)[0];

    if (
      target === "" ||
      target.startsWith("#") ||
      /^(?:https?:|mailto:)/i.test(target)
    ) {
      continue;
    }

    const pathOnly = target.split("#", 1)[0].split("?", 1)[0];
    let decoded;
    try {
      decoded = decodeURIComponent(pathOnly);
    } catch {
      errors.push(`${source}: invalid-relative-link-encoding`);
      continue;
    }

    const resolved = resolve(dirname(absoluteSource), decoded);
    const repositoryRelative = relative(ROOT, resolved);
    if (
      repositoryRelative === ".." ||
      repositoryRelative.startsWith(`..${sep}`) ||
      isAbsolute(repositoryRelative)
    ) {
      errors.push(`${source}: relative-link-outside-repository`);
      continue;
    }
    if (!existsSync(resolved)) {
      errors.push(
        `${source}: missing-relative-link -> ${relativeFromRoot(resolved)}`,
      );
    }
  }
}

for (const requirement of REQUIRED_LINKS) {
  const path = resolve(ROOT, requirement.file);
  if (!existsSync(path)) {
    errors.push(`${requirement.file}: ${requirement.rule}-source-missing`);
    continue;
  }
  if (!readFileSync(path, "utf8").includes(requirement.value)) {
    errors.push(`${requirement.file}: ${requirement.rule}-missing`);
  }
}

if (errors.length > 0) {
  console.error("Documentation check failed:");
  for (const error of [...new Set(errors)].sort()) {
    console.error(error);
  }
  process.exit(1);
}

console.log(
  `Documentation check passed (${REQUIRED_AUTHORITIES.length} authorities, ${markdownFiles.length} Markdown files).`,
);
