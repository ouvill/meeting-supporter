import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const errors = [];

const read = (path) => readFile(resolve(root, path), "utf8");
const requireText = (path, content, expected) => {
  if (!content.includes(expected)) {
    errors.push(`${path}: missing required policy text`);
  }
};

const [cla, contributing, pullRequestTemplate, packageSource] =
  await Promise.all([
    read("CLA.md"),
    read("CONTRIBUTING.md"),
    read(".github/PULL_REQUEST_TEMPLATE.md"),
    read("package.json"),
  ]);

for (const expected of [
  "**Version 1.0 — Published 14 July 2026**",
  "**Ouvill** (`contact@ouvill.net`)",
  "https://github.com/ouvill/meeting-supporter",
  "commercial, or proprietary licenses",
  "GNU Affero General Public License v3.0 only (`AGPL-3.0-only`)",
  "Electronic acceptance and records maintained by CLA Assistant constitute Your signature",
  "The Tokyo District Court has exclusive jurisdiction",
]) {
  requireText("CLA.md", cla, expected);
}
if (/\[[A-Z][A-Z0-9_]+\]/.test(cla)) {
  errors.push("CLA.md: unresolved agreement placeholder found");
}
if (/\bguardian\b/i.test(cla)) {
  errors.push("CLA.md: guardian acceptance conflicts with adult-only signing");
}

for (const expected of [
  "[Meeting Supporter Contributor License Agreement](CLA.md)",
  "https://cla-assistant.io/",
  "license/cla",
  "A pull-request checkbox is not a signature.",
  "grants Ouvill the additional rights needed to sublicense and relicense",
]) {
  requireText("CONTRIBUTING.md", contributing, expected);
}

for (const expected of [
  "[Meeting Supporter CLA](../CLA.md)",
  "they do not constitute a CLA signature",
  "license/cla",
]) {
  requireText(
    ".github/PULL_REQUEST_TEMPLATE.md",
    pullRequestTemplate,
    expected,
  );
}

let packageJson;
try {
  packageJson = JSON.parse(packageSource);
} catch {
  errors.push("package.json: invalid JSON");
}
if (packageJson?.license !== "AGPL-3.0-only") {
  errors.push("package.json: license must be AGPL-3.0-only");
}
if (packageJson?.private !== true) {
  errors.push("package.json: private must remain true");
}

if (errors.length > 0) {
  console.error("CLA policy check failed:");
  for (const error of errors) console.error(`- ${error}`);
  process.exitCode = 1;
} else {
  const digest = createHash("sha256").update(cla).digest("hex");
  console.log(`CLA policy check passed (version 1.0, sha256 ${digest})`);
}
